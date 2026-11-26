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

/// The digest of every byte read so far, and whether the file grew after an end of file.
#[derive(Default)]
struct Hashed {
    hasher: Sha256,
    at_end: bool,
    grew: bool,
}

/// Hashes every byte read through it into state that outlives the reader.
struct HashingReader<'a, R> {
    inner: R,
    hashed: &'a mut Hashed,
}

impl<R: Read> Read for HashingReader<'_, R> {
    fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
        let read = self.inner.read(buf)?;
        let hashed = &mut *self.hashed;
        hashed.hasher.update(&buf[..read]);
        if read == 0 {
            hashed.at_end = true;
        } else if hashed.at_end {
            hashed.grew = true;
        }
        Ok(read)
    }
}

/// Parse a market file while hashing it, so its bytes are never held in memory at once.
///
/// The digest is compared after the last byte. A file that fails to parse is still read to
/// the end first, so a replaced file reports the digest mismatch before any content error.
pub fn read_market(path: &Path, digest: &str) -> Result<SnapshotData, MarketFileError> {
    let mut file = File::open(path)?;
    stream_market(&mut file, digest)?.map_err(|streamed| {
        MarketFileError::Parse(in_memory_error(path, digest).unwrap_or(streamed))
    })
}

/// Parse and hash `source`; the digest is checked first, the parse result is returned after.
///
/// The parser sees a prefix of the bytes hashed. If a read reported end of file and a later
/// read still returned bytes, the file grew in place while it was read: the parser may have
/// stopped at the earlier end, so the digest does not describe what was parsed. Reading the
/// whole file first would have hashed only the shorter file, so that is a digest mismatch.
fn stream_market<R: Read>(
    source: &mut R,
    digest: &str,
) -> Result<Result<SnapshotData, serde_json::Error>, MarketFileError> {
    let mut hashed = Hashed::default();
    // The parser must own the `BufReader`: std reads single bytes from an owned `BufReader`
    // straight out of its buffer, but not through `&mut BufReader`, which halves throughput.
    let reader = HashingReader {
        inner: &mut *source,
        hashed: &mut hashed,
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
        inner: source,
        hashed: &mut hashed,
    };
    io::copy(&mut rest, &mut io::sink())?;
    if hashed.grew || format!("{:x}", hashed.hasher.finalize()) != digest {
        return Err(MarketFileError::Digest);
    }
    Ok(parsed)
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

#[cfg(test)]
mod tests {
    use super::*;

    /// A file that reports end of file once, then has `tail` appended in place.
    struct Growing {
        head: io::Cursor<Vec<u8>>,
        tail: Option<Vec<u8>>,
    }

    impl Read for Growing {
        fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
            let read = self.head.read(buf)?;
            if read == 0
                && let Some(tail) = self.tail.take()
            {
                // Report this end of file; the appended bytes are read next time.
                let at = self.head.position();
                let mut bytes = std::mem::take(self.head.get_mut());
                bytes.extend(tail);
                self.head = io::Cursor::new(bytes);
                self.head.set_position(at);
            }
            Ok(read)
        }
    }

    fn market() -> Vec<u8> {
        br#"{"cash_dividends":[],"instruments":[],"next_session":"2024-01-10","rows":[],"sessions":["2024-01-09"]}"#.to_vec()
    }

    fn stream(
        head: &[u8],
        tail: &[u8],
    ) -> Result<Result<SnapshotData, serde_json::Error>, MarketFileError> {
        let whole = [head, tail].concat();
        let digest = format!("{:x}", Sha256::digest(&whole));
        let mut source = Growing {
            head: io::Cursor::new(head.to_vec()),
            tail: (!tail.is_empty()).then(|| tail.to_vec()),
        };
        stream_market(&mut source, &digest)
    }

    #[test]
    fn a_complete_file_parses() {
        assert!(matches!(stream(&market(), b""), Ok(Ok(_))));
    }

    #[test]
    fn bytes_appended_after_a_complete_parse_are_a_digest_mismatch() {
        // The final file is invalid (trailing characters) but its digest matches.
        assert!(matches!(
            stream(&market(), b"garbage"),
            Err(MarketFileError::Digest)
        ));
    }

    #[test]
    fn a_file_still_being_written_is_a_digest_mismatch_not_a_content_error() {
        // The parser hits end of file mid-value, then the rest of the valid file arrives.
        let valid = market();
        let (head, tail) = valid.split_at(40);
        let got = stream(head, tail);
        assert!(matches!(got, Err(MarketFileError::Digest)), "{got:?}");
    }

    #[test]
    fn a_syntax_error_before_the_end_is_still_a_content_error() {
        let mut invalid = market();
        invalid[1] = b'!';
        assert!(matches!(stream(&invalid, b""), Ok(Err(_))));
    }
}
