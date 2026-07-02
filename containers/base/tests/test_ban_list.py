from __future__ import annotations

import pytest
from mmc_base.ban_list import SageMakerContaminationError, assert_clean_env


def test_clean_env_passes():
    assert_clean_env({"PROJECT_NAME": "p"})


def test_sm_env_raises():
    with pytest.raises(SageMakerContaminationError):
        assert_clean_env({"SM_MODEL_DIR": "/opt/ml/model"})


def test_sagemaker_env_raises():
    with pytest.raises(SageMakerContaminationError):
        assert_clean_env({"SAGEMAKER_JOB_NAME": "x"})


def test_multiple_reported():
    with pytest.raises(SageMakerContaminationError) as exc:
        assert_clean_env({"SM_A": "1", "SAGEMAKER_B": "2"})
    assert "SM_A" in str(exc.value)
    assert "SAGEMAKER_B" in str(exc.value)
