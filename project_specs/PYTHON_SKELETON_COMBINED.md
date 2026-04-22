# Python skeleton combined


## __init__.py

```python

```


## acoustics/__init__.py

```python

```


## acoustics/air.py

```python
from dataclasses import dataclass


@dataclass
class AirProperties:
    rho: float
    c: float
    temperature_c: float
    humidity_percent: float

```


## acoustics/features.py

```python
class FeatureExtractor:
    def extract(self, freq_hz, zin_mag, peaks, confidence_score: float) -> dict:
        f0 = peaks[0]["frequency_hz"] if peaks else None
        return {
            "f0_hz": f0,
            "peak_count": len(peaks),
            "model_confidence": confidence_score,
        }

```


## acoustics/impedance.py

```python
import numpy as np


class ImpedanceAnalyzer:
    def magnitude_phase(self, zin):
        return np.abs(zin), np.angle(zin)

    def band_statistics(self, freq_hz, zin_mag, bands):
        stats = {}
        for band in bands:
            mask = (freq_hz >= band["f_min_hz"]) & (freq_hz <= band["f_max_hz"])
            if mask.any():
                stats[band["name"]] = {
                    "mean": float(np.mean(zin_mag[mask])),
                    "max": float(np.max(zin_mag[mask])),
                }
        return stats

```


## acoustics/losses.py

```python
import numpy as np


class LossModel:
    def complex_wavenumber(self, omega: np.ndarray, diameter_m: float, material: dict, c: float) -> np.ndarray:
        beta = material["acoustic_model"]["beta_nominal"]
        alpha = 1e-5 * beta * np.sqrt(omega) / max(diameter_m, 1e-6)
        return omega / c - 1j * alpha

```


## acoustics/peaks.py

```python
import numpy as np


class PeakExtractor:
    def find_peaks(self, freq_hz, zin_mag, config=None):
        peaks = []
        for i in range(1, len(zin_mag) - 1):
            if zin_mag[i] > zin_mag[i - 1] and zin_mag[i] > zin_mag[i + 1]:
                peaks.append({"index": i, "frequency_hz": float(freq_hz[i]), "height": float(zin_mag[i])})
        return peaks

    def estimate_q(self, freq_hz, zin_mag, peak_index: int) -> float:
        return float(freq_hz[peak_index] / max(1.0, 5.0))

```


## acoustics/radiation.py

```python
import numpy as np


class RadiationModel:
    def end_correction_m(self, radius_m: float) -> float:
        return 0.613 * radius_m

    def radiation_impedance(self, omega, radius_m: float, rho: float, c: float):
        zc = rho * c / (np.pi * radius_m**2)
        k = omega / c
        return zc * ((k * radius_m) ** 2 / 4.0 + 1j * k * self.end_correction_m(radius_m))

```


## acoustics/transfer_matrix.py

```python
import numpy as np


class TransferMatrixModel:
    def segment_matrix(self, zc: np.ndarray, k: np.ndarray, length_m: float):
        A = np.cos(k * length_m)
        B = 1j * zc * np.sin(k * length_m)
        C = 1j * (1 / zc) * np.sin(k * length_m)
        D = np.cos(k * length_m)
        return A, B, C, D

    def input_impedance_placeholder(self, freq_hz: np.ndarray) -> np.ndarray:
        # Placeholder until full TMM chain is implemented.
        return 1e6 / (1 + 1j * freq_hz / 200)

```


## acoustics/transverse.py

```python
import math


class TransverseModeChecker:
    def cutoff_hz(self, diameter_m: float, c: float) -> float:
        a = diameter_m / 2.0
        return 1.84 * c / (2 * math.pi * max(a, 1e-9))

    def confidence_score(self, max_diameter_m: float, fmax_hz: float, c: float) -> float:
        fc = self.cutoff_hz(max_diameter_m, c)
        if fc >= fmax_hz:
            return 1.0
        return max(0.0, min(1.0, fc / fmax_hz))

```


## config/__init__.py

```python

```


## config/loader.py

```python
from pathlib import Path
import yaml


def load_yaml(path: str | Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

```


## config/schema.py

```python
from dataclasses import dataclass, field
from typing import Literal, Dict, Any


@dataclass
class RangeFloat:
    min: float
    max: float


@dataclass
class DiameterConstraint:
    min: float
    max: float
    step: float


@dataclass
class GeometryConstraints:
    total_length_cm: RangeFloat
    max_segments: int
    min_segment_length_cm: float
    diameter_cm: DiameterConstraint
    allow_steps: bool
    allow_reverse_taper: bool
    allow_local_constrictions: bool
    allow_local_expansions: bool


@dataclass
class ObjectiveConfig:
    enabled: bool
    weight: float
    direction: Literal["minimize", "maximize", "match"]
    hard_constraint: bool = False
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RuntimeEstimationConfig:
    enabled: bool
    method: Literal["analytic_plus_microbenchmark"]
    benchmark_samples_linear: int
    benchmark_samples_nonlinear: int
    display_before_launch: bool


@dataclass
class ProjectConfig:
    geometry_constraints: GeometryConstraints
    objectives: Dict[str, ObjectiveConfig]
    runtime_estimation: RuntimeEstimationConfig
    raw: Dict[str, Any]

```


## config/validators.py

```python
def validate_config_dict(cfg: dict) -> list[str]:
    errors: list[str] = []
    if "geometry_constraints" not in cfg:
        errors.append("Missing geometry_constraints")
    if "materials" not in cfg:
        errors.append("Missing materials")
    if "objectives" not in cfg:
        errors.append("Missing objectives")
    return errors

```


## geometry/__init__.py

```python

```


## geometry/builders.py

```python
from .models import Design, Segment


class DesignBuilder:
    def build(self, genome: dict) -> Design:
        segments = genome.get("segments", [])
        design = Design(id=genome.get("id", "design"), segments=segments, metadata={})
        return self.assign_positions(design)

    def assign_positions(self, design: Design) -> Design:
        x = 0.0
        for seg in design.segments:
            seg.position_start_cm = x
            x += seg.length_cm
            seg.position_end_cm = x
        return design

```


## geometry/constraints.py

```python
class GeometryValidator:
    def validate(self, design, config) -> list[str]:
        errors: list[str] = []
        if not design.segments:
            errors.append("Design has no segments")
        return errors

    def soft_penalties(self, design, config) -> dict[str, float]:
        return {"segment_count_penalty": max(0, len(design.segments) - 8) * 0.01}

```


## geometry/discretization.py

```python
class GeometryDiscretizer:
    def discretize(self, design, max_segment_cm: float = 1.0):
        return design

```


## geometry/models.py

```python
from dataclasses import dataclass
from typing import Optional, Literal

SegmentKind = Literal[
    "mouthpiece",
    "cylinder",
    "cone",
    "flare_conical",
    "flare_exponential",
    "flare_powerlaw",
    "branch",
    "helmholtz_neck",
]


@dataclass
class Segment:
    kind: SegmentKind
    length_cm: float
    d_in_cm: float
    d_out_cm: float
    material_id: str
    profile_params: Optional[dict] = None
    position_start_cm: float = 0.0
    position_end_cm: float = 0.0


@dataclass
class Design:
    id: str
    segments: list[Segment]
    metadata: dict

```


## materials/__init__.py

```python

```


## materials/database.py

```python
from pathlib import Path
import yaml


class MaterialDatabase:
    def __init__(self, raw: dict):
        self.raw = raw
        self.materials = {m["id"]: m for m in raw.get("materials", [])}

    @classmethod
    def from_yaml(cls, path: str | Path) -> "MaterialDatabase":
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls(data)

    def get(self, material_id: str) -> dict:
        return self.materials[material_id]

```


## materials/models.py

```python
from dataclasses import dataclass
from typing import Optional, Literal


@dataclass
class AcousticParameter:
    nominal: float
    min: float
    max: float
    status: Literal["sourced", "inferred", "to_calibrate"]
    confidence: Literal["high", "medium", "low"]


@dataclass
class MaterialVariant:
    humidity_state: Optional[str] = None
    finish: Optional[str] = None
    grade: Optional[str] = None
    knot_class: Optional[str] = None
    density_class: Optional[str] = None


@dataclass
class Material:
    id: str
    base_material: str
    family: str
    subtype: str
    variant: Optional[MaterialVariant]

    beta: AcousticParameter
    porosity_leak: AcousticParameter
    wall_loss: AcousticParameter

    manufacturability: str
    cost_level: str
    mass_level: str

    recommended_for_mouthpiece: bool
    recommended_for_body: bool
    recommended_for_bell: bool

```


## materials/uncertainty.py

```python
import random


class MaterialUncertaintyManager:
    def sample_parameter(self, nominal: float, low: float, high: float) -> float:
        return random.triangular(low, high, nominal)

    def calibration_priority_score(self, uncertainty: float, sensitivity: float, objective_weight: float) -> float:
        return uncertainty * sensitivity * objective_weight

```


## materials/variants.py

```python
from copy import deepcopy


class MaterialVariantGenerator:
    def __init__(self, rules: dict):
        self.rules = rules

    def generate_variant(self, base_material: dict, *, humidity_state=None, finish=None, grade=None, knot_class=None, density_class=None) -> dict:
        material = deepcopy(base_material)
        variant = {
            "humidity_state": humidity_state,
            "finish": finish,
            "grade": grade,
            "knot_class": knot_class,
            "density_class": density_class,
        }
        material["generated_variant"] = variant
        return self.apply_modifiers(material, variant)

    def apply_modifiers(self, material: dict, variant: dict) -> dict:
        model = material.get("acoustic_model", {})
        hum = self.rules.get("wood_humidity_states", {}).get(variant.get("humidity_state") or "airdry", {})
        fin = self.rules.get("wood_finishes", {}).get(variant.get("finish") or "raw", {})
        grd = self.rules.get("wood_grade", {}).get(variant.get("grade") or "clear", {})

        for key, factor_key in [("beta_nominal", "beta_factor"), ("porosity_leak_nominal", "porosity_factor"), ("wall_loss_nominal", "wall_loss_factor")]:
            factor = hum.get(factor_key, 1.0) * fin.get(factor_key, 1.0) * grd.get(factor_key, 1.0)
            if key in model:
                model[key] *= factor
        return material

```


## nonlinear/__init__.py

```python

```


## nonlinear/lips.py

```python
class LipModel:
    def derivatives(self, t, state, params, p_acoustic):
        return state

```


## nonlinear/regimes.py

```python
class RegimeAnalyzer:
    def analyze(self, simulation_result) -> dict:
        return {"stable": False, "regime": "unknown"}

```


## nonlinear/resonator_td.py

```python
class TimeDomainResonator:
    def from_linear_model(self, design, config):
        return self

    def pressure_from_flow(self, flow_signal):
        return flow_signal

```


## nonlinear/thresholds.py

```python
class OscillationThresholdEstimator:
    def estimate_threshold(self, design, config) -> dict:
        return {"threshold_pressure_kpa": None, "onset_detected": False}

```


## optimization/__init__.py

```python

```


## optimization/objectives.py

```python
class ObjectiveEvaluator:
    def score_objectives(self, features: dict, config) -> dict[str, float]:
        return {"total_placeholder": 0.0}

    def hard_constraints_ok(self, features: dict, config) -> bool:
        return True

    def penalties(self, design, features, config) -> dict[str, float]:
        return {}

```


## optimization/pareto.py

```python
class ParetoOptimizer:
    def run(self, evaluator, search_space, config) -> list[dict]:
        return []

```


## optimization/runtime_estimator.py

```python
class RuntimeEstimator:
    def analytical_estimate(self, config) -> dict:
        return {
            "low_seconds": 0,
            "expected_seconds": 0,
            "high_seconds": 0,
            "dominant_factors": [],
        }

    def benchmark(self, config, pipeline) -> dict:
        return {"benchmark_ran": False}

    def combined_estimate(self, config, pipeline) -> dict:
        est = self.analytical_estimate(config)
        est["confidence"] = "low"
        return est

```


## optimization/search_space.py

```python
class SearchSpace:
    def sample_random(self) -> dict:
        return {"id": "candidate", "segments": []}

    def decode(self, genome: dict) -> dict:
        return genome

```


## optimization/selector.py

```python
class FinalSelector:
    def select_best(self, ranked_designs: list[dict], method: str) -> dict:
        return ranked_designs[0] if ranked_designs else {}

```


## pipeline/__init__.py

```python

```


## pipeline/evaluate_linear.py

```python
import numpy as np
from acoustics.transfer_matrix import TransferMatrixModel
from acoustics.impedance import ImpedanceAnalyzer
from acoustics.peaks import PeakExtractor
from acoustics.features import FeatureExtractor


class LinearEvaluationPipeline:
    def evaluate(self, design, config) -> dict:
        freq_hz = np.linspace(10, 5000, 4096)
        zin = TransferMatrixModel().input_impedance_placeholder(freq_hz)
        zin_mag, zin_phase = ImpedanceAnalyzer().magnitude_phase(zin)
        peaks = PeakExtractor().find_peaks(freq_hz, zin_mag, config)
        features = FeatureExtractor().extract(freq_hz, zin_mag, peaks, confidence_score=1.0)
        return {
            "design_id": getattr(design, "id", "unknown"),
            "freq_hz": freq_hz,
            "zin": zin,
            "zin_mag": zin_mag,
            "zin_phase": zin_phase,
            "peaks": peaks,
            "features": features,
            "valid": True,
        }

```


## pipeline/evaluate_nonlinear.py

```python
class NonlinearPipeline:
    def evaluate(self, design_result, config) -> dict:
        design_result["nonlinear"] = {"status": "not_implemented"}
        return design_result

```


## pipeline/evaluate_robustness.py

```python
class RobustnessPipeline:
    def evaluate(self, design_result, config) -> dict:
        design_result["robustness"] = {"status": "not_implemented"}
        return design_result

```


## pipeline/run_optimizer.py

```python
from config.loader import load_yaml
from optimization.runtime_estimator import RuntimeEstimator


class OptimizationRunner:
    def run(self, config_path: str):
        config = load_yaml(config_path)
        estimate = RuntimeEstimator().combined_estimate(config, pipeline=None)
        return {"config_loaded": True, "runtime_estimate": estimate}

```


## player/__init__.py

```python

```


## player/models.py

```python
import random


class PlayerProfileSampler:
    def sample_beginner(self, n: int) -> list[dict]:
        return [{"type": "beginner", "mouth_pressure_kpa": random.uniform(0.2, 2.0)} for _ in range(n)]

    def sample_expert(self, n: int) -> list[dict]:
        return [{"type": "expert", "mouth_pressure_kpa": random.uniform(0.2, 4.0)} for _ in range(n)]

```


## player/robustness.py

```python
class RobustnessEvaluator:
    def evaluate(self, design_result: dict) -> dict:
        return {"robustness_placeholder": True, "base_id": design_result.get("design_id")}

```


## player/vocal_tract.py

```python
class VocalTractLibrary:
    def get_preset(self, name: str) -> dict:
        presets = {
            "neutral": {"id": "neutral"},
            "tongue_high": {"id": "tongue_high"},
            "tongue_low": {"id": "tongue_low"},
            "narrow_front": {"id": "narrow_front"},
            "wide_open": {"id": "wide_open"},
        }
        return presets[name]

```


## reporting/__init__.py

```python

```


## reporting/export.py

```python
import json
import csv


class ExportManager:
    def export_json(self, results, path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

    def export_csv(self, results, path):
        if not results:
            return
        keys = sorted(results[0].keys())
        with open(path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=keys)
            writer.writeheader()
            writer.writerows(results)

```


## reporting/plots.py

```python
class PlotManager:
    def plot_impedance(self, result, path):
        return path

    def plot_radiation(self, result, path):
        return path

    def plot_pareto(self, results, path):
        return path

```


## reporting/ranking.py

```python
class RankingEngine:
    def rank(self, evaluated_designs: list[dict], config=None) -> list[dict]:
        return evaluated_designs

```


## reporting/summaries.py

```python
class SummaryGenerator:
    def summarize_design(self, result: dict, language: str = "fr") -> str:
        return "Résumé automatique non encore implémenté."

```


## tests/__init__.py

```python

```
