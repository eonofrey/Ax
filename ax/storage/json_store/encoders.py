#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

import re
import warnings
from pathlib import Path
from typing import Any, Dict, Type

from ax.benchmark.benchmark_problem import (
    BenchmarkProblem,
    MultiObjectiveBenchmarkProblem,
    SingleObjectiveBenchmarkProblem,
)
from ax.benchmark.problems.hpo.torchvision import PyTorchCNNTorchvisionBenchmarkProblem
from ax.core import ObservationFeatures
from ax.core.arm import Arm
from ax.core.batch_trial import BatchTrial
from ax.core.data import Data
from ax.core.experiment import Experiment
from ax.core.generator_run import GeneratorRun
from ax.core.map_data import MapData, MapKeyInfo
from ax.core.metric import Metric
from ax.core.multi_type_experiment import MultiTypeExperiment
from ax.core.objective import MultiObjective, Objective, ScalarizedObjective
from ax.core.optimization_config import (
    MultiObjectiveOptimizationConfig,
    OptimizationConfig,
)
from ax.core.outcome_constraint import OutcomeConstraint
from ax.core.parameter import ChoiceParameter, FixedParameter, RangeParameter
from ax.core.parameter_constraint import (
    OrderConstraint,
    ParameterConstraint,
    SumConstraint,
)
from ax.core.parameter_distribution import ParameterDistribution
from ax.core.risk_measures import RiskMeasure
from ax.core.runner import Runner
from ax.core.search_space import RobustSearchSpace, SearchSpace
from ax.core.trial import Trial
from ax.early_stopping.strategies import (
    LogicalEarlyStoppingStrategy,
    PercentileEarlyStoppingStrategy,
    ThresholdEarlyStoppingStrategy,
)
from ax.exceptions.core import AxStorageWarning
from ax.exceptions.storage import JSONEncodeError
from ax.global_stopping.strategies.improvement import ImprovementGlobalStoppingStrategy
from ax.modelbridge.generation_node import GenerationNode
from ax.modelbridge.generation_strategy import GenerationStep, GenerationStrategy
from ax.modelbridge.model_spec import FactoryFunctionModelSpec, ModelSpec
from ax.modelbridge.registry import _encode_callables_as_references
from ax.modelbridge.transforms.base import Transform
from ax.modelbridge.transition_criterion import TransitionCriterion
from ax.models.torch.botorch_modular.model import BoTorchModel
from ax.models.torch.botorch_modular.surrogate import Surrogate
from ax.models.winsorization_config import WinsorizationConfig
from ax.storage.botorch_modular_registry import CLASS_TO_REGISTRY
from ax.storage.transform_registry import TRANSFORM_REGISTRY
from ax.utils.common.constants import Keys
from ax.utils.common.serialization import serialize_init_args
from ax.utils.common.typeutils import not_none
from ax.utils.common.typeutils_torch import torch_type_to_str
from botorch.models.transforms.input import ChainedInputTransform, InputTransform
from torch import Tensor


def experiment_to_dict(experiment: Experiment) -> Dict[str, Any]:
    """Convert Ax experiment to a dictionary."""
    return {
        "__type": experiment.__class__.__name__,
        "name": experiment._name,
        "description": experiment.description,
        "experiment_type": experiment.experiment_type,
        "search_space": experiment.search_space,
        "optimization_config": experiment.optimization_config,
        "tracking_metrics": list(experiment._tracking_metrics.values()),
        "runner": experiment.runner,
        "status_quo": experiment.status_quo,
        "time_created": experiment.time_created,
        "trials": experiment.trials,
        "is_test": experiment.is_test,
        "data_by_trial": experiment.data_by_trial,
        "properties": experiment._properties,
        "default_data_type": experiment._default_data_type,
    }


def multi_type_experiment_to_dict(experiment: MultiTypeExperiment) -> Dict[str, Any]:
    """Convert AE multitype experiment to a dictionary."""
    multi_type_dict = {
        "default_trial_type": experiment._default_trial_type,
        "_metric_to_canonical_name": experiment._metric_to_canonical_name,
        "_metric_to_trial_type": experiment._metric_to_trial_type,
        "_trial_type_to_runner": experiment._trial_type_to_runner,
    }
    multi_type_dict.update(experiment_to_dict(experiment))
    return multi_type_dict


def batch_to_dict(batch: BatchTrial) -> Dict[str, Any]:
    """Convert Ax batch to a dictionary."""
    return {
        "__type": batch.__class__.__name__,
        "index": batch.index,
        "trial_type": batch.trial_type,
        "ttl_seconds": batch.ttl_seconds,
        "status": batch.status,
        "status_quo": batch.status_quo,
        "status_quo_weight_override": batch._status_quo_weight_override,
        "time_created": batch.time_created,
        "time_completed": batch.time_completed,
        "time_staged": batch.time_staged,
        "time_run_started": batch.time_run_started,
        "abandoned_reason": batch.abandoned_reason,
        "failed_reason": batch.failed_reason,
        "run_metadata": batch.run_metadata,
        "stop_metadata": batch.stop_metadata,
        "generator_run_structs": batch.generator_run_structs,
        "runner": batch.runner,
        "abandoned_arms_metadata": batch._abandoned_arms_metadata,
        "num_arms_created": batch._num_arms_created,
        "optimize_for_power": batch.optimize_for_power,
        "generation_step_index": batch._generation_step_index,
        "lifecycle_stage": batch.lifecycle_stage,
        "properties": batch._properties,
    }


def benchmark_problem_to_dict(benchmark_problem: BenchmarkProblem) -> Dict[str, Any]:
    """Convert Ax benchmark problem to a dictionary."""
    return {
        "__type": benchmark_problem.__class__.__name__,
        "name": benchmark_problem.name,
        "search_space": benchmark_problem.search_space,
        "optimization_config": benchmark_problem.optimization_config,
        "runner": benchmark_problem.runner,
        "num_trials": benchmark_problem.num_trials,
        "infer_noise": benchmark_problem.infer_noise,
        "tracking_metrics": benchmark_problem.tracking_metrics,
    }


def multi_objective_benchmark_problem_to_dict(
    moo_benchmark_problem: MultiObjectiveBenchmarkProblem,
) -> Dict[str, Any]:
    """Convert Ax multi-objective benchmark problem to a dictionary."""
    return {
        "__type": moo_benchmark_problem.__class__.__name__,
        "name": moo_benchmark_problem.name,
        "search_space": moo_benchmark_problem.search_space,
        "optimization_config": moo_benchmark_problem.optimization_config,
        "runner": moo_benchmark_problem.runner,
        "num_trials": moo_benchmark_problem.num_trials,
        "infer_noise": moo_benchmark_problem.infer_noise,
        "tracking_metrics": moo_benchmark_problem.tracking_metrics,
        "maximum_hypervolume": moo_benchmark_problem.maximum_hypervolume,
        "reference_point": moo_benchmark_problem.reference_point,
    }


def single_objective_benchmark_problem_to_dict(
    soo_benchmark_problem: SingleObjectiveBenchmarkProblem,
) -> Dict[str, Any]:
    return {
        "__type": soo_benchmark_problem.__class__.__name__,
        "name": soo_benchmark_problem.name,
        "search_space": soo_benchmark_problem.search_space,
        "optimization_config": soo_benchmark_problem.optimization_config,
        "runner": soo_benchmark_problem.runner,
        "num_trials": soo_benchmark_problem.num_trials,
        "infer_noise": soo_benchmark_problem.infer_noise,
        "tracking_metrics": soo_benchmark_problem.tracking_metrics,
        "optimal_value": soo_benchmark_problem.optimal_value,
    }


def trial_to_dict(trial: Trial) -> Dict[str, Any]:
    """Convert Ax trial to a dictionary."""
    return {
        "__type": trial.__class__.__name__,
        "index": trial.index,
        "trial_type": trial.trial_type,
        "ttl_seconds": trial.ttl_seconds,
        "status": trial.status,
        "time_created": trial.time_created,
        "time_completed": trial.time_completed,
        "time_staged": trial.time_staged,
        "time_run_started": trial.time_run_started,
        "abandoned_reason": trial.abandoned_reason,
        "failed_reason": trial.failed_reason,
        "run_metadata": trial.run_metadata,
        "stop_metadata": trial.stop_metadata,
        "generator_run": trial.generator_run,
        "runner": trial.runner,
        "num_arms_created": trial._num_arms_created,
        "generation_step_index": trial._generation_step_index,
        "properties": trial._properties,
    }


def range_parameter_to_dict(parameter: RangeParameter) -> Dict[str, Any]:
    """Convert Ax range parameter to a dictionary."""
    return {
        "__type": parameter.__class__.__name__,
        "name": parameter.name,
        "parameter_type": parameter.parameter_type,
        "lower": parameter.lower,
        "upper": parameter.upper,
        "log_scale": parameter.log_scale,
        "logit_scale": parameter.logit_scale,
        "digits": parameter.digits,
        "is_fidelity": parameter.is_fidelity,
        "target_value": parameter.target_value,
    }


def choice_parameter_to_dict(parameter: ChoiceParameter) -> Dict[str, Any]:
    """Convert Ax choice parameter to a dictionary."""
    return {
        "__type": parameter.__class__.__name__,
        "is_ordered": parameter.is_ordered,
        "is_task": parameter.is_task,
        "name": parameter.name,
        "parameter_type": parameter.parameter_type,
        "values": parameter.values,
        "is_fidelity": parameter.is_fidelity,
        "target_value": parameter.target_value,
        "dependents": parameter.dependents if parameter.is_hierarchical else None,
    }


def fixed_parameter_to_dict(parameter: FixedParameter) -> Dict[str, Any]:
    """Convert Ax fixed parameter to a dictionary."""
    return {
        "__type": parameter.__class__.__name__,
        "name": parameter.name,
        "parameter_type": parameter.parameter_type,
        "value": parameter.value,
        "is_fidelity": parameter.is_fidelity,
        "target_value": parameter.target_value,
        "dependents": parameter.dependents if parameter.is_hierarchical else None,
    }


def order_parameter_constraint_to_dict(
    parameter_constraint: OrderConstraint,
) -> Dict[str, Any]:
    """Convert Ax order parameter constraint to a dictionary."""
    return {
        "__type": parameter_constraint.__class__.__name__,
        "lower_name": parameter_constraint.lower_parameter.name,
        "upper_name": parameter_constraint.upper_parameter.name,
    }


def sum_parameter_constraint_to_dict(
    parameter_constraint: SumConstraint,
) -> Dict[str, Any]:
    """Convert Ax sum parameter constraint to a dictionary."""
    return {
        "__type": parameter_constraint.__class__.__name__,
        "parameter_names": parameter_constraint._parameter_names,
        "is_upper_bound": parameter_constraint._is_upper_bound,
        # SumParameterConstraint constructor takes in absolute value of
        # the bound and transforms it based on the is_upper_bound value
        "bound": abs(parameter_constraint._bound),
    }


def parameter_constraint_to_dict(
    parameter_constraint: ParameterConstraint,
) -> Dict[str, Any]:
    """Convert Ax sum parameter constraint to a dictionary."""
    return {
        "__type": parameter_constraint.__class__.__name__,
        "constraint_dict": parameter_constraint.constraint_dict,
        "bound": parameter_constraint.bound,
    }


def arm_to_dict(arm: Arm) -> Dict[str, Any]:
    """Convert Ax arm to a dictionary."""
    return {
        "__type": arm.__class__.__name__,
        "parameters": arm.parameters,
        "name": arm._name,
    }


def search_space_to_dict(search_space: SearchSpace) -> Dict[str, Any]:
    """Convert Ax search space to a dictionary."""
    return {
        "__type": search_space.__class__.__name__,
        "parameters": list(search_space.parameters.values()),
        "parameter_constraints": search_space.parameter_constraints,
    }


def robust_search_space_to_dict(rss: RobustSearchSpace) -> Dict[str, Any]:
    """Convert robust search space to a dictionary."""
    return {
        "__type": rss.__class__.__name__,
        "parameters": list(rss._parameters.values()),
        "parameter_distributions": rss.parameter_distributions,
        "num_samples": rss.num_samples,
        "environmental_variables": list(rss._environmental_variables.values()),
        "parameter_constraints": rss.parameter_constraints,
    }


def parameter_distribution_to_dict(dist: ParameterDistribution) -> Dict[str, Any]:
    """Convert a parameter distribution to a dictionary."""
    return {
        "__type": dist.__class__.__name__,
        "parameters": dist.parameters,
        "distribution_class": dist.distribution_class,
        "distribution_parameters": dist.distribution_parameters,
        "multiplicative": dist.multiplicative,
    }


def metric_to_dict(metric: Metric) -> Dict[str, Any]:
    """Convert Ax metric to a dictionary."""
    properties = metric.serialize_init_args(obj=metric)
    properties["__type"] = metric.__class__.__name__
    return properties


def objective_to_dict(objective: Objective) -> Dict[str, Any]:
    """Convert Ax objective to a dictionary."""
    return {
        "__type": objective.__class__.__name__,
        "metric": objective.metric,
        "minimize": objective.minimize,
    }


def multi_objective_to_dict(objective: MultiObjective) -> Dict[str, Any]:
    """Convert Ax objective to a dictionary."""
    return {
        "__type": objective.__class__.__name__,
        "objectives": objective.objectives,
        "weights": objective.weights,
    }


def scalarized_objective_to_dict(objective: ScalarizedObjective) -> Dict[str, Any]:
    """Convert Ax objective to a dictionary."""
    return {
        "__type": objective.__class__.__name__,
        "metrics": objective.metrics,
        "weights": objective.weights,
        "minimize": objective.minimize,
    }


def outcome_constraint_to_dict(outcome_constraint: OutcomeConstraint) -> Dict[str, Any]:
    """Convert Ax outcome constraint to a dictionary."""
    return {
        "__type": outcome_constraint.__class__.__name__,
        "metric": outcome_constraint.metric,
        "op": outcome_constraint.op,
        "bound": outcome_constraint.bound,
        "relative": outcome_constraint.relative,
    }


def optimization_config_to_dict(
    optimization_config: OptimizationConfig,
) -> Dict[str, Any]:
    """Convert Ax optimization config to a dictionary."""
    return {
        "__type": optimization_config.__class__.__name__,
        "objective": optimization_config.objective,
        "outcome_constraints": optimization_config.outcome_constraints,
        "risk_measure": optimization_config.risk_measure,
    }


def multi_objective_optimization_config_to_dict(
    multi_objective_optimization_config: MultiObjectiveOptimizationConfig,
) -> Dict[str, Any]:
    """Convert Ax optimization config to a dictionary."""
    return {
        "__type": multi_objective_optimization_config.__class__.__name__,
        "objective": multi_objective_optimization_config.objective,
        "outcome_constraints": multi_objective_optimization_config.outcome_constraints,
        "objective_thresholds": multi_objective_optimization_config.objective_thresholds,  # noqa E501
        "risk_measure": multi_objective_optimization_config.risk_measure,
    }


def generator_run_to_dict(generator_run: GeneratorRun) -> Dict[str, Any]:
    """Convert Ax generator run to a dictionary."""
    gr = generator_run
    cand_metadata = gr.candidate_metadata_by_arm_signature
    return {
        "__type": gr.__class__.__name__,
        "arms": gr.arms,
        "weights": gr.weights,
        "optimization_config": gr.optimization_config,
        "search_space": gr.search_space,
        "time_created": gr.time_created,
        "model_predictions": gr.model_predictions,
        "best_arm_predictions": gr.best_arm_predictions,
        "generator_run_type": gr.generator_run_type,
        "index": gr.index,
        "fit_time": gr.fit_time,
        "gen_time": gr.gen_time,
        "model_key": gr._model_key,
        "model_kwargs": gr._model_kwargs,
        "bridge_kwargs": gr._bridge_kwargs,
        "gen_metadata": gr._gen_metadata,
        "model_state_after_gen": gr._model_state_after_gen,
        "generation_step_index": gr._generation_step_index,
        "candidate_metadata_by_arm_signature": cand_metadata,
        "generation_node_name": gr._generation_node_name,
    }


def runner_to_dict(runner: Runner) -> Dict[str, Any]:
    """Convert Ax runner to a dictionary."""
    properties = runner.serialize_init_args(obj=runner)
    properties["__type"] = runner.__class__.__name__
    return properties


def data_to_dict(data: Data) -> Dict[str, Any]:
    """Convert Ax data to a dictionary."""
    properties = data.serialize_init_args(obj=data)
    properties["__type"] = data.__class__.__name__
    return properties


def map_data_to_dict(map_data: MapData) -> Dict[str, Any]:
    """Convert Ax map data to a dictionary."""
    properties = map_data.serialize_init_args(obj=map_data)
    properties["__type"] = map_data.__class__.__name__
    return properties


# pyre-fixme[24]: Generic type `MapKeyInfo` expects 1 type parameter.
def map_key_info_to_dict(mki: MapKeyInfo) -> Dict[str, Any]:
    """Convert Ax map data metadata to a dictionary."""
    properties = serialize_init_args(obj=mki)
    properties["__type"] = mki.__class__.__name__
    return properties


def transform_type_to_dict(transform_type: Type[Transform]) -> Dict[str, Any]:
    """Convert a transform class to a dictionary."""
    return {
        "__type": "Type[Transform]",
        "index_in_registry": TRANSFORM_REGISTRY[transform_type],
        "transform_type": f"{transform_type}",
    }


def generation_step_to_dict(generation_step: GenerationStep) -> Dict[str, Any]:
    """Converts Ax generation step to a dictionary."""
    return {
        "__type": generation_step.__class__.__name__,
        "model": generation_step.model,
        "num_trials": generation_step.num_trials,
        "min_trials_observed": generation_step.min_trials_observed,
        "completion_criteria": generation_step.completion_criteria,
        "max_parallelism": generation_step.max_parallelism,
        "use_update": generation_step.use_update,
        "enforce_num_trials": generation_step.enforce_num_trials,
        "model_kwargs": _encode_callables_as_references(
            generation_step.model_kwargs or {}
        ),
        "model_gen_kwargs": _encode_callables_as_references(
            generation_step.model_gen_kwargs or {}
        ),
        "index": generation_step.index,
        "should_deduplicate": generation_step.should_deduplicate,
        "transition_criteria": generation_step.transition_criteria,
    }


def generation_node_to_dict(generation_node: GenerationNode) -> Dict[str, Any]:
    """Convert Ax generation node to a dictionary."""
    return {
        "__type": generation_node.__class__.__name__,
        "model_specs": generation_node.model_specs,
        "should_deduplicate": generation_node.should_deduplicate,
        "node_name": generation_node.node_name,
        "gen_unlimited_trials": generation_node.gen_unlimited_trials,
        "model_spec_to_gen_from": generation_node._model_spec_to_gen_from,
        "transition_criteria": generation_node.transition_criteria,
    }


def generation_strategy_to_dict(
    generation_strategy: GenerationStrategy,
) -> Dict[str, Any]:
    """Converts Ax generation strategy to a dictionary."""
    if generation_strategy.uses_non_registered_models:
        raise ValueError(
            "Generation strategies that use custom models provided through "
            "callables cannot be serialized and stored."
        )
    node_based_gs = generation_strategy.is_node_based
    return {
        "__type": generation_strategy.__class__.__name__,
        "db_id": generation_strategy._db_id,
        "name": generation_strategy.name,
        "steps": generation_strategy._steps if not node_based_gs else [],
        "curr_index": (
            generation_strategy.current_step_index if not node_based_gs else -1
        ),
        "generator_runs": generation_strategy._generator_runs,
        "had_initialized_model": generation_strategy.model is not None,
        "experiment": generation_strategy._experiment,
        "nodes": generation_strategy._nodes,
        "curr_node_name": generation_strategy.current_node_name,
    }


def transition_criterion_to_dict(criterion: TransitionCriterion) -> Dict[str, Any]:
    """Convert Ax TransitionCriterion to a dictionary."""
    properties = criterion.serialize_init_args(obj=criterion)
    properties["__type"] = criterion.__class__.__name__
    return properties


def model_spec_to_dict(model_spec: ModelSpec) -> Dict[str, Any]:
    """Convert Ax model spec to a dictionary."""
    if isinstance(model_spec, FactoryFunctionModelSpec):
        raise NotImplementedError(
            f"JSON serialization not yet implemented for model spec: {model_spec}"
            " because it leverages a factory function instead of `Models` registry."
        )
    return {
        "__type": model_spec.__class__.__name__,
        "model_enum": model_spec.model_enum,
        "model_kwargs": model_spec.model_kwargs,
        "model_gen_kwargs": model_spec.model_gen_kwargs,
    }


def observation_features_to_dict(obs_features: ObservationFeatures) -> Dict[str, Any]:
    """Converts Ax observation features to a dictionary"""
    return {
        "__type": obs_features.__class__.__name__,
        "parameters": obs_features.parameters,
        "trial_index": obs_features.trial_index,
        "start_time": obs_features.start_time,
        "end_time": obs_features.end_time,
        "random_split": obs_features.random_split,
        "metadata": obs_features.metadata,
    }


def botorch_model_to_dict(model: BoTorchModel) -> Dict[str, Any]:
    """Convert Ax model to a dictionary."""
    return {
        "__type": model.__class__.__name__,
        "acquisition_class": model.acquisition_class,
        "acquisition_options": model.acquisition_options or {},
        "surrogate": (
            model._surrogates[Keys.ONLY_SURROGATE]
            if Keys.ONLY_SURROGATE in model._surrogates
            else None
        ),
        "surrogate_specs": (
            model.surrogate_specs if len(model.surrogate_specs) > 0 else None
        ),
        "botorch_acqf_class": model._botorch_acqf_class,
        "refit_on_update": model.refit_on_update,
        "refit_on_cv": model.refit_on_cv,
        "warm_start_refit": model.warm_start_refit,
    }


def surrogate_to_dict(surrogate: Surrogate) -> Dict[str, Any]:
    """Convert Ax surrogate to a dictionary."""
    dict_representation = {"__type": surrogate.__class__.__name__}
    dict_representation.update(surrogate._serialize_attributes_as_kwargs())
    return dict_representation


def tensor_to_dict(obj: Tensor) -> Dict[str, Any]:
    if obj.numel() > 1e4:
        warnings.warn(
            f"Attempting to serialize a tensor with {obj.numel()} elements. "
            "This may result in storage issues.",
            AxStorageWarning,
        )
    return {
        "__type": "Tensor",
        "value": obj.tolist(),
        "dtype": {"__type": "torch_dtype", "value": torch_type_to_str(obj.dtype)},
        "device": {"__type": "torch_device", "value": torch_type_to_str(obj.device)},
    }


# pyre-fixme[2]: Parameter annotation cannot contain `Any`.
def botorch_modular_to_dict(class_type: Type[Any]) -> Dict[str, Any]:
    """Convert any class to a dictionary."""
    for _class in CLASS_TO_REGISTRY:
        if issubclass(class_type, _class):
            registry = CLASS_TO_REGISTRY[_class]
            if class_type not in registry:
                raise ValueError(
                    f"Class `{class_type.__name__}` not in Type[{_class.__name__}] "
                    "registry, please add it. BoTorch object registries are "
                    "located in `ax/storage/botorch_modular_registry.py`."
                )
            return {
                "__type": f"Type[{_class.__name__}]",
                "index": registry[class_type],
                "class": f"{_class}",
            }
    raise ValueError(
        f"{class_type} does not have a corresponding parent class in "
        "CLASS_TO_REGISTRY."
    )


# pyre-fixme[2]: Parameter annotation cannot contain `Any`.
def botorch_component_to_dict(input_obj: Any) -> Dict[str, Any]:
    class_type = input_obj.__class__
    if isinstance(input_obj, InputTransform):
        # Input transforms cannot be initialized with their state dicts.
        # We will instead extract the init args.
        state_dict = botorch_input_transform_to_init_args(input_transform=input_obj)
    else:
        state_dict = dict(input_obj.state_dict())
    return {
        "__type": f"{class_type.__name__}",
        "index": class_type,
        "class": f"{class_type}",
        "state_dict": state_dict,
    }


def botorch_input_transform_to_init_args(
    input_transform: InputTransform,
) -> Dict[str, Any]:
    """Extract the init kwargs from an input transform."""
    if isinstance(input_transform, ChainedInputTransform):
        return {k: botorch_component_to_dict(v) for k, v in input_transform.items()}
    else:
        try:
            return input_transform.get_init_args()  # pyre-fixme [16]
        except AttributeError:
            raise JSONEncodeError(
                f"{input_transform.__class__.__name__} does not define `get_init_args` "
                "method. Please implement it to enable storage."
            )


def percentile_early_stopping_strategy_to_dict(
    strategy: PercentileEarlyStoppingStrategy,
) -> Dict[str, Any]:
    """Convert Ax percentile early stopping strategy to a dictionary."""
    return {
        "__type": strategy.__class__.__name__,
        "metric_names": strategy.metric_names,
        "percentile_threshold": strategy.percentile_threshold,
        "min_progression": strategy.min_progression,
        "min_curves": strategy.min_curves,
        "trial_indices_to_ignore": strategy.trial_indices_to_ignore,
        "seconds_between_polls": strategy.seconds_between_polls,
        "normalize_progressions": strategy.normalize_progressions,
    }


def threshold_early_stopping_strategy_to_dict(
    strategy: ThresholdEarlyStoppingStrategy,
) -> Dict[str, Any]:
    """Convert Ax metric-threshold early stopping strategy to a dictionary."""
    return {
        "__type": strategy.__class__.__name__,
        "metric_names": strategy.metric_names,
        "metric_threshold": strategy.metric_threshold,
        "min_progression": strategy.min_progression,
        "trial_indices_to_ignore": strategy.trial_indices_to_ignore,
        "normalize_progressions": strategy.normalize_progressions,
    }


def logical_early_stopping_strategy_to_dict(
    strategy: LogicalEarlyStoppingStrategy,
) -> Dict[str, Any]:
    return {
        "__type": strategy.__class__.__name__,
        "left": strategy.left,
        "right": strategy.right,
    }


def improvement_global_stopping_strategy_to_dict(
    gss: ImprovementGlobalStoppingStrategy,
) -> Dict[str, Any]:
    """Convert ImprovementGlobalStoppingStrategy to a dictionary."""
    return {
        "__type": gss.__class__.__name__,
        "min_trials": gss.min_trials,
        "window_size": gss.window_size,
        "improvement_bar": gss.improvement_bar,
        "inactive_when_pending_trials": gss.inactive_when_pending_trials,
    }


def winsorization_config_to_dict(config: WinsorizationConfig) -> Dict[str, Any]:
    """Convert Ax winsorization config to a dictionary."""
    return {
        "__type": config.__class__.__name__,
        "lower_quantile_margin": config.lower_quantile_margin,
        "upper_quantile_margin": config.upper_quantile_margin,
        "lower_boundary": config.lower_boundary,
        "upper_boundary": config.upper_boundary,
    }


def pytorch_cnn_torchvision_benchmark_problem_to_dict(
    problem: PyTorchCNNTorchvisionBenchmarkProblem,
) -> Dict[str, Any]:
    # unit tests for this in benchmark suite
    return {
        "__type": problem.__class__.__name__,
        "name": not_none(re.compile("(?<=::).*").search(problem.name)).group(),
        "num_trials": problem.num_trials,
        "infer_noise": problem.infer_noise,
    }


def risk_measure_to_dict(
    risk_measure: RiskMeasure,
) -> Dict[str, Any]:
    """Convert a RiskMeasure to a dictionary."""
    return {
        "__type": risk_measure.__class__.__name__,
        "risk_measure": risk_measure.risk_measure,
        "options": risk_measure.options,
    }


def pathlib_to_dict(path: Path) -> Dict[str, Any]:
    return {"__type": path.__class__.__name__, "pathsegments": [str(path)]}
