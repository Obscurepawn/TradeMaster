use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use std::path::Path;

#[pyfunction]
fn run_event<'py>(py: Python<'py>, request: &[u8]) -> PyResult<Bound<'py, PyBytes>> {
    let bytes = py
        .detach(|| tm_runner::run_json(request))
        .map_err(|err| PyValueError::new_err(err.to_string()))?;
    Ok(PyBytes::new(py, &bytes))
}

#[pyfunction]
fn replay_request<'py>(
    py: Python<'py>,
    request: &[u8],
    base_dir: &str,
) -> PyResult<Bound<'py, PyBytes>> {
    let bytes = py
        .detach(|| tm_runner::replay_request(request, Path::new(base_dir)))
        .map_err(|err| PyValueError::new_err(err.to_string()))?;
    Ok(PyBytes::new(py, &bytes))
}

pub(super) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(run_event, module)?)?;
    module.add_function(wrap_pyfunction!(replay_request, module)?)
}
