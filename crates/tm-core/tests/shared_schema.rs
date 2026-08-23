use arrow_array::RecordBatch;
use arrow_schema::{DataType, TimeUnit};
use parquet::arrow::ArrowWriter;
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::sync::Arc;
use time::OffsetDateTime;
use tm_core::{
    RUN_ARTIFACT_SCHEMA_JSON, RUN_MANIFEST_FIXTURE_JSON, RUN_MANIFEST_SCHEMA_JSON,
    RunArtifactObject, RunManifest, run_artifact_schemas,
};

fn artifact_objects() -> Vec<RunArtifactObject> {
    run_artifact_schemas()
        .keys()
        .map(|name| {
            RunArtifactObject::try_new(name, format!("{name}.parquet"), "c".repeat(64), 0).unwrap()
        })
        .collect()
}

fn golden_type(name: &str) -> DataType {
    match name {
        "utf8" => DataType::Utf8,
        "timestamp_us_utc" => DataType::Timestamp(TimeUnit::Microsecond, Some("UTC".into())),
        "decimal128_38_8" => DataType::Decimal128(38, 8),
        "decimal128_38_0" => DataType::Decimal128(38, 0),
        "uint8" => DataType::UInt8,
        other => panic!("unsupported golden type {other}"),
    }
}

#[test]
fn rust_run_manifest_matches_the_shared_required_fields() {
    let golden: serde_json::Value = serde_json::from_str(RUN_MANIFEST_SCHEMA_JSON).unwrap();
    let manifest = RunManifest::try_new(
        "r1",
        OffsetDateTime::UNIX_EPOCH,
        "a".repeat(64),
        "strategy",
        "engine",
        "b".repeat(64),
        artifact_objects(),
    )
    .unwrap();
    let value = serde_json::to_value(manifest).unwrap();
    for field in golden["required"].as_array().unwrap() {
        assert!(value.get(field.as_str().unwrap()).is_some());
    }
    assert_eq!(value["manifest_version"], "run/v1");
}

#[test]
fn rust_run_manifest_matches_and_reads_the_canonical_wire_fixture() {
    let manifest = RunManifest::try_new(
        "r1",
        OffsetDateTime::UNIX_EPOCH,
        "a".repeat(64),
        "strategy",
        "0.1.0",
        "b".repeat(64),
        artifact_objects(),
    )
    .unwrap();
    assert_eq!(
        serde_json::to_string(&manifest).unwrap(),
        RUN_MANIFEST_FIXTURE_JSON.trim()
    );
    let decoded: RunManifest = serde_json::from_str(RUN_MANIFEST_FIXTURE_JSON).unwrap();
    assert_eq!(decoded, manifest);
    let serialized = run_artifact_schemas()
        .into_iter()
        .map(|(name, schema)| {
            let mut writer =
                ArrowWriter::try_new(Vec::new(), Arc::new(schema.clone()), None).unwrap();
            writer
                .write(&RecordBatch::new_empty(Arc::new(schema)))
                .unwrap();
            let bytes = writer.into_inner().unwrap();
            (name, bytes)
        })
        .collect::<BTreeMap<_, _>>();
    let bound_objects = serialized
        .iter()
        .map(|(name, bytes)| {
            RunArtifactObject::try_new(
                name,
                format!("{name}.parquet"),
                format!("{:x}", Sha256::digest(bytes)),
                0,
            )
            .unwrap()
        })
        .collect();
    let bound = RunManifest::try_new(
        "r1",
        OffsetDateTime::UNIX_EPOCH,
        "a".repeat(64),
        "strategy",
        "0.1.0",
        "b".repeat(64),
        bound_objects,
    )
    .unwrap();
    bound.verify_artifacts(&serialized).unwrap();
    let mut wrong_bytes = serialized;
    wrong_bytes.insert("signals".to_owned(), vec![1]);
    assert!(bound.verify_artifacts(&wrong_bytes).is_err());
    assert!(
        RunManifest::try_new(
            "r1",
            OffsetDateTime::UNIX_EPOCH + time::Duration::nanoseconds(1),
            "a".repeat(64),
            "strategy",
            "0.1.0",
            "b".repeat(64),
            artifact_objects(),
        )
        .is_err()
    );
    let mut unknown: serde_json::Value = serde_json::from_str(RUN_MANIFEST_FIXTURE_JSON).unwrap();
    unknown["unexpected"] = serde_json::json!(true);
    assert!(serde_json::from_value::<RunManifest>(unknown).is_err());
    for invalid_time in ["1970-01-01T00:00:00+00:00", "1970-01-01T00:00:00.0000000Z"] {
        let invalid = RUN_MANIFEST_FIXTURE_JSON.replace("1970-01-01T00:00:00Z", invalid_time);
        assert!(serde_json::from_str::<RunManifest>(&invalid).is_err());
    }
}

#[test]
fn rust_embeds_the_versioned_shared_artifact_schema() {
    let value: serde_json::Value = serde_json::from_str(RUN_ARTIFACT_SCHEMA_JSON).unwrap();
    assert_eq!(value["schema_id"], "trademaster.run-artifacts/v1");
    assert_eq!(value["records"].as_object().unwrap().len(), 11);
    assert_eq!(value["sort_keys"].as_object().unwrap().len(), 11);

    let rust_schemas = run_artifact_schemas();
    for (name, fields) in value["records"].as_object().unwrap() {
        let schema = rust_schemas.get(name).unwrap();
        assert_eq!(
            schema.metadata().get("trademaster.schema_id").unwrap(),
            "trademaster.run-artifacts/v1"
        );
        let fields = fields.as_array().unwrap();
        assert_eq!(schema.fields().len(), fields.len());
        for (rust_field, golden_field) in schema.fields().iter().zip(fields) {
            assert_eq!(rust_field.name(), golden_field["name"].as_str().unwrap());
            assert_eq!(
                rust_field.data_type(),
                &golden_type(golden_field["type"].as_str().unwrap())
            );
            assert_eq!(
                rust_field.is_nullable(),
                golden_field["nullable"].as_bool().unwrap()
            );
        }
    }
}
