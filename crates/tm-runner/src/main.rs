use std::io::{self, Read, Write};

fn main() {
    let mut input = Vec::new();
    if let Err(error) = io::stdin().read_to_end(&mut input) {
        eprintln!("tm-runner: {error}");
        std::process::exit(2);
    }
    match tm_runner::run_json(&input) {
        Ok(output) => {
            if let Err(error) = io::stdout().write_all(&output) {
                eprintln!("tm-runner: {error}");
                std::process::exit(2);
            }
        }
        Err(error) => {
            eprintln!("tm-runner: {error}");
            std::process::exit(1);
        }
    }
}
