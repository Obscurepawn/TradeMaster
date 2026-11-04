use std::{env, fs, io, path::PathBuf};

fn main() -> io::Result<()> {
    let source = PathBuf::from("../tm-core/schemas");
    println!("cargo:rerun-if-changed={}", source.display());
    let target = PathBuf::from(env::var_os("OUT_DIR").expect("Cargo sets OUT_DIR")).join("schemas");
    if target.exists() {
        fs::remove_dir_all(&target)?;
    }
    fs::create_dir(&target)?;
    for entry in fs::read_dir(source)? {
        let entry = entry?;
        let path = entry.path();
        if path
            .extension()
            .is_some_and(|extension| extension == "json")
        {
            fs::copy(&path, target.join(entry.file_name()))?;
        }
    }
    Ok(())
}
