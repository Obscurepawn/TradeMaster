//! Read a content-identified market file and run a portfolio of target weights.

use crate::RunnerError;
use serde::Deserialize;
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::fs::File;
use std::io::{self, BufReader, Read};
use std::path::{Path, PathBuf};
use thiserror::Error;
use tm_engine::snapshot::{SnapshotData, SnapshotRun, run_snapshot};

/// Read buffer between the file and the JSON parser.
const READ_BUFFER: usize = 1 << 20;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Request {
    schema_id: String,
    snapshot_path: PathBuf,
    snapshot_sha256: String,
    snapshot_id: Option<String>,
    run: SnapshotRun,
}

fn valid_digest(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

fn check_manifest(market: &Path, digest: &str, snapshot_id: &str) -> Result<(), RunnerError> {
    let invalid = || RunnerError::InvalidRequest("snapshot manifest identity mismatch".into());
    let file = market.with_file_name("manifest.json");
    let bytes = std::fs::read(file).map_err(|err| {
        RunnerError::InvalidRequest(format!("cannot read snapshot manifest: {err}"))
    })?;
    let mut manifest: serde_json::Map<String, Value> = serde_json::from_slice(&bytes)
        .map_err(|err| RunnerError::InvalidRequest(format!("invalid snapshot manifest: {err}")))?;
    let recorded = manifest.remove("snapshot_id").ok_or_else(invalid)?;
    let descriptor = manifest.get("market").ok_or_else(invalid)?;
    if recorded.as_str() != Some(snapshot_id)
        || manifest.get("schema_id").and_then(Value::as_str)
            != Some("trademaster.daily-snapshot/v1")
        || descriptor["sha256"].as_str() != Some(digest)
        || descriptor["file"].as_str() != market.file_name().and_then(|n| n.to_str())
    {
        return Err(invalid());
    }
    let canonical =
        serde_json::to_vec(&manifest).map_err(|err| RunnerError::Serialization(err.to_string()))?;
    if format!("{:x}", Sha256::digest(canonical)) != snapshot_id {
        return Err(invalid());
    }
    Ok(())
}

/// Why a content-identified market file could not be loaded.
#[derive(Debug, Error)]
pub enum MarketFileError {
    #[error("cannot read market snapshot: {0}")]
    Read(#[from] io::Error),
    #[error("market snapshot SHA-256 mismatch")]
    Digest,
    #[error("invalid market snapshot: {0}")]
    Parse(serde_json::Error),
}

/// Hashes every byte read through it into a hasher that outlives the reader.
struct HashingReader<'a, R> {
    inner: R,
    hasher: &'a mut Sha256,
}

impl<R: Read> Read for HashingReader<'_, R> {
    fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
        let read = self.inner.read(buf)?;
        self.hasher.update(&buf[..read]);
        Ok(read)
    }
}

/// Parse a market file while hashing it, so its bytes are never held in memory at once.
///
/// The digest is compared after the last byte. A file that fails to parse is still read to
/// the end first, so a replaced file reports the digest mismatch before any content error.
pub fn read_market(path: &Path, digest: &str) -> Result<SnapshotData, MarketFileError> {
    let file = File::open(path)?;
    let mut hasher = Sha256::new();
    // The parser must own the `BufReader`: std reads single bytes from an owned `BufReader`
    // straight out of its buffer, but not through `&mut BufReader`, which halves throughput.
    let reader = HashingReader {
        inner: &file,
        hasher: &mut hasher,
    };
    let mut parser =
        serde_json::Deserializer::from_reader(BufReader::with_capacity(READ_BUFFER, reader));
    let parsed = SnapshotData::deserialize(&mut parser).and_then(|data| {
        parser.end()?;
        Ok(data)
    });
    // Buffered bytes were hashed when read; hash whatever the parser left in the file.
    drop(parser);
    let mut rest = HashingReader {
        inner: &file,
        hasher: &mut hasher,
    };
    io::copy(&mut rest, &mut io::sink())?;
    if format!("{:x}", hasher.finalize()) != digest {
        return Err(MarketFileError::Digest);
    }
    parsed.map_err(|streamed| {
        MarketFileError::Parse(in_memory_error(path, digest).unwrap_or(streamed))
    })
}

/// The error an in-memory parse of the same verified bytes reports.
///
/// A stream parser counts a byte it has only peeked at, so some errors would name a column
/// one past the in-memory position. Invalid files are rare, so only they are read whole.
fn in_memory_error(path: &Path, digest: &str) -> Option<serde_json::Error> {
    let bytes = std::fs::read(path).ok()?;
    if format!("{:x}", Sha256::digest(&bytes)) != digest {
        return None;
    }
    serde_json::from_slice::<SnapshotData>(&bytes).err()
}

/// Read a market snapshot and verify its content and optional manifest identity.
pub fn load_market(
    market: &Path,
    digest: &str,
    snapshot_id: Option<&str>,
) -> Result<SnapshotData, RunnerError> {
    if !valid_digest(digest) || snapshot_id.is_some_and(|id| !valid_digest(id)) {
        return Err(RunnerError::InvalidRequest(
            "invalid snapshot digest".into(),
        ));
    }
    if let Some(id) = snapshot_id {
        check_manifest(market, digest, id)?;
    }
    read_market(market, digest).map_err(|err| RunnerError::InvalidRequest(err.to_string()))
}

/// Run the saved targets with market paths resolved beside the request file.
pub fn run_snapshot_json(input: &[u8], directory: &Path) -> Result<Vec<u8>, RunnerError> {
    let req: Request = serde_json::from_slice(input)
        .map_err(|err| RunnerError::InvalidRequest(err.to_string()))?;
    if req.schema_id != "trademaster.snapshot-run/v1" {
        return Err(RunnerError::InvalidRequest(
            "invalid snapshot schema".into(),
        ));
    }
    let data = load_market(
        &directory.join(req.snapshot_path),
        &req.snapshot_sha256,
        req.snapshot_id.as_deref(),
    )?;
    let result =
        run_snapshot(data, &req.run).map_err(|err| RunnerError::Execution(err.to_string()))?;
    serde_json::to_vec(&result).map_err(|err| RunnerError::Serialization(err.to_string()))
}
