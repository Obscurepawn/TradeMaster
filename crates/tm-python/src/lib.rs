//! In-process Python bindings for TradeMaster's Rust calculations.
mod replay;

use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pythonize::{depythonize, pythonize};
use self_cell::self_cell;
use tm_core::PositionSnapshot;
use tm_engine::snapshot::{
    PreparedMarket, SnapshotAccount, SnapshotData, SnapshotError, SnapshotRun, SnapshotSession,
};

fn execution_error(err: SnapshotError) -> PyErr {
    PyRuntimeError::new_err(err.to_string())
}

self_cell! {
    struct IndexedMarket {
        owner: SnapshotData,
        #[covariant]
        dependent: PreparedMarket,
    }
}

/// Cash and holdings at a single valued close. Positions are sorted by instrument ID.
#[pyclass(frozen, module = "trademaster._native")]
struct NativeAccount {
    cash: i128,
    equity: i128,
    dividend_receivable: i128,
    positions: Vec<PositionSnapshot>,
}

impl NativeAccount {
    fn from_close(account: SnapshotAccount) -> Self {
        Self {
            cash: account.cash.scaled(),
            equity: account.equity.scaled(),
            dividend_receivable: account.dividend_receivable.scaled(),
            positions: account.positions,
        }
    }

    fn position(&self, symbol: &str) -> Option<&PositionSnapshot> {
        let positions = &self.positions;
        positions
            .binary_search_by(|p| p.instrument_id().0.as_str().cmp(symbol))
            .ok()
            .map(|i| &positions[i])
    }
}

#[pymethods]
impl NativeAccount {
    #[getter]
    fn cash(&self) -> i128 {
        self.cash
    }

    #[getter]
    fn dividend_receivable(&self) -> i128 {
        self.dividend_receivable
    }

    #[getter]
    fn equity(&self) -> i128 {
        self.equity
    }

    fn quantity(&self, symbol: &str) -> i128 {
        self.position(symbol).map_or(0, |p| p.quantity().units())
    }

    fn sellable_quantity(&self, symbol: &str) -> i128 {
        self.position(symbol)
            .map_or(0, |p| p.sellable_quantity().units())
    }
}

#[pyclass(frozen, module = "trademaster._native")]
struct Market {
    indexed: IndexedMarket,
}

#[pymethods]
impl Market {
    #[new]
    fn new(py: Python<'_>, content: &[u8]) -> PyResult<Self> {
        py.detach(|| {
            let data: SnapshotData = serde_json::from_slice(content)
                .map_err(|err| PyValueError::new_err(format!("invalid market snapshot: {err}")))?;
            let indexed = IndexedMarket::try_new(data, |data| PreparedMarket::new(data))
                .map_err(execution_error)?;
            Ok(Self { indexed })
        })
    }

    #[pyo3(signature = (run, decision_indices, callback=None))]
    fn run<'py>(
        &self,
        py: Python<'py>,
        run: &Bound<'py, PyAny>,
        decision_indices: Vec<usize>,
        callback: Option<Py<PyAny>>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let run: SnapshotRun = depythonize(run)?;
        let market = self.indexed.borrow_dependent();
        if decision_indices
            .last()
            .is_some_and(|i| *i >= market.sessions().len())
            || decision_indices.windows(2).any(|pair| pair[0] >= pair[1])
            || (callback.is_none() && !decision_indices.is_empty())
            || (callback.is_some() && !run.targets.is_empty())
        {
            return Err(PyValueError::new_err(
                "supply ordered decision sessions and either a strategy or recorded targets",
            ));
        }
        let result = py.detach(|| -> PyResult<_> {
            let mut dates = decision_indices.into_iter().peekable();
            let mut session = SnapshotSession::new(market, &run, dates.peek().is_some())
                .map_err(execution_error)?;
            for i in 0..market.sessions().len() {
                let Some(account) = session.advance().map_err(execution_error)? else {
                    break;
                };
                if dates.next_if_eq(&i).is_some() {
                    let callback = callback
                        .as_ref()
                        .expect("a decision schedule has a callback");
                    let target = Python::attach(|py| -> PyResult<_> {
                        let fills = pythonize(py, session.new_fills())?;
                        let decision =
                            callback.call1(py, (i, NativeAccount::from_close(account), fills))?;
                        Ok(depythonize(decision.bind(py))?)
                    })?;
                    if let Some(target) = target {
                        session.submit(target).map_err(execution_error)?;
                    }
                }
            }
            Ok(session.finish())
        })?;
        Ok(pythonize(py, &result)?)
    }
}

#[pymodule]
fn _native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<Market>()?;
    module.add_class::<NativeAccount>()?;
    replay::register(module)
}
