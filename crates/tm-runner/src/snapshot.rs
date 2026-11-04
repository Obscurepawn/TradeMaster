//! Read a content-identified market file and run a portfolio of target weights.

use crate::RunnerError;
use serde::Deserialize;
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::path::{Path, PathBuf};
use tm_engine::snapshot::{SnapshotData, SnapshotRun, run_snapshot};

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
    let bytes = std::fs::read(market).map_err(|err| {
        RunnerError::InvalidRequest(format!("cannot read market snapshot: {err}"))
    })?;
    if format!("{:x}", Sha256::digest(&bytes)) != digest {
        return Err(RunnerError::InvalidRequest(
            "market snapshot SHA-256 mismatch".into(),
        ));
    }
    serde_json::from_slice(&bytes)
        .map_err(|err| RunnerError::InvalidRequest(format!("invalid market snapshot: {err}")))
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
        run_snapshot(&data, &req.run).map_err(|err| RunnerError::Execution(err.to_string()))?;
    serde_json::to_vec(&result).map_err(|err| RunnerError::Serialization(err.to_string()))
}
