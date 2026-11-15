"""AMEND-002: prediction-time guards against log-link extrapolation."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import model as model_mod  # noqa: E402
from model import ReceivingModel  # noqa: E402


def toy():
    # mu_rec = 2 * e^z, mu_ypr = 12 (z = standardized feature x); stays under the model's own e^4 ceiling
    return ReceivingModel(rec_params=np.array([np.log(2.0), 1.0]), ypr_params=np.array([np.log(12.0), 0.0]),
                          alpha=0.1097, k=1.555, means=np.array([0.0]), sds=np.array([1.0]), cols=["x"])


def test_outlying_feature_is_clipped():
    p = toy().predict_params(pd.DataFrame({"x": [10.0, 1.0]}))
    assert p.mu_rec.iloc[0] == pytest.approx(2 * np.exp(model_mod.FEATURE_Z_CLIP))   # not 2 * e^10
    assert p.mu_rec.iloc[1] == pytest.approx(2 * np.e)                                # inside the clip: unchanged


def test_receptions_capped_at_catch_rate_of_forecast_targets():
    df = pd.DataFrame({"x": [2.0, 2.0, 2.0, 2.0], "f_targets": [14.2, 40.0, 2.0, np.nan]})
    p = toy().predict_params(df).mu_rec.to_numpy()
    raw = 2 * np.exp(2.0)                                          # 14.8
    assert p[0] == pytest.approx(model_mod.MAX_CATCH_RATE * 14.2)  # capped
    assert p[1] == pytest.approx(raw)                              # cap above the projection: no effect
    assert p[2] == pytest.approx(raw)                              # under CAP_MIN_TARGETS: not capped
    assert p[3] == pytest.approx(raw)                              # unknown targets: not capped


def test_no_targets_column_means_no_cap():
    p = toy().predict_params(pd.DataFrame({"x": [2.0]}))
    assert p.mu_rec.iloc[0] == pytest.approx(2 * np.exp(2.0))
