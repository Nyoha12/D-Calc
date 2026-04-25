from .evaluate_linear import LinearEvaluationPipeline, evaluate
from .evaluate_robustness import RobustnessPipeline, evaluate as evaluate_robustness, evaluate_batch as evaluate_robustness_batch
from .evaluate_nonlinear import NonlinearPipeline, evaluate as evaluate_nonlinear, evaluate_batch as evaluate_nonlinear_batch

_RUN_OPTIMIZER_EXPORTS = {
    "OptimizerRunner",
    "run",
    "load_context",
    "estimate_runtime",
    "run_linear_phase",
    "run_robustness_phase",
    "run_nonlinear_phase",
    "finalize",
}


def __getattr__(name: str):
    if name in _RUN_OPTIMIZER_EXPORTS:
        from . import run_optimizer

        return getattr(run_optimizer, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "LinearEvaluationPipeline",
    "evaluate",
    "RobustnessPipeline",
    "evaluate_robustness",
    "evaluate_robustness_batch",
    "NonlinearPipeline",
    "evaluate_nonlinear",
    "evaluate_nonlinear_batch",
    "OptimizerRunner",
    "run",
    "load_context",
    "estimate_runtime",
    "run_linear_phase",
    "run_robustness_phase",
    "run_nonlinear_phase",
    "finalize",
]
