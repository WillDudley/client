import logging
import sys
import wandb
from wandb.errors import ExecutionException

from .agent import LaunchAgent
from .runner import loader
from .utils import (
    PROJECT_DOCKER_ARGS,
    PROJECT_STORAGE_DIR,
    PROJECT_SYNCHRONOUS,
    fetch_and_validate_project,
    _is_wandb_local_uri,
)

_logger = logging.getLogger(__name__)


def push_to_queue(api, queue, run_spec):
    try:
        res = api.push_to_run_queue(queue, run_spec)
    except Exception as e:
        print("Exception:", e)
        return None
    return res


def run_agent(spec, queues=None):
    if not spec or len(spec) != 1 or len(spec[0].split("/")) != 2:
        wandb.termerror("Specify agent spec in the form: 'entity/project'")
        sys.exit(1)
    spec = spec[0]
    entity, project = spec.split("/")

    agent = LaunchAgent(entity, project, queues)
    agent.loop()


def _run(
    uri,
    experiment_name,
    wandb_project,
    wandb_entity,
    docker_image,
    entry_point,
    version,
    parameters,
    docker_args,
    runner_name,
    runner_config,
    storage_dir,
    synchronous,
    api=None,
):
    """
    Helper that delegates to the project-running method corresponding to the passed-in backend.
    Returns a ``SubmittedRun`` corresponding to the project run.
    """
    project = fetch_and_validate_project(
        uri, experiment_name, api, runner_name, version, entry_point, parameters
    )

    if wandb_project is None:
        wandb_project = api.settings("project")
    if wandb_entity is None:
        wandb_entity = api.settings("entity")

    project.docker_env["WANDB_PROJECT"] = wandb_project
    project.docker_env["WANDB_ENTITY"] = wandb_entity

    runner_config[PROJECT_SYNCHRONOUS] = synchronous
    runner_config[PROJECT_DOCKER_ARGS] = docker_args
    runner_config[PROJECT_STORAGE_DIR] = storage_dir
    if docker_image:
        runner_config["DOCKER_IMAGE"] = docker_image

    backend = loader.load_backend(runner_name, api)
    if backend:
        submitted_run = backend.run(project, runner_config)
        return submitted_run
    else:
        raise ExecutionException(
            "Unavailable backend {}, available backends: {}".format(
                runner_name, ", ".join(loader.WANDB_RUNNERS.keys())
            )
        )


def run(
    uri,
    entry_point=None,
    version=None,
    parameters=None,
    docker_args=None,
    experiment_name=None,
    resource="local",
    wandb_project=None,
    wandb_entity=None,
    docker_image=None,
    config=None,
    storage_dir=None,
    synchronous=True,
    api=None,
):
    """
    Run a W&B project. The project can be local or stored at a Git URI.
    W&B provides built-in support for running projects locally or remotely on a Databricks or
    Kubernetes cluster. You can also run projects against other targets by installing an appropriate
    third-party plugin. See `Community Plugins <../plugins.html#community-plugins>`_ for more
    information.
    For information on using this method in chained workflows, see `Building Multistep Workflows
    <../projects.html#building-multistep-workflows>`_.
    :raises: :py:class:`wandb.exceptions.ExecutionException` If a run launched in blocking mode
             is unsuccessful.
    :param uri: URI of project to run. A local filesystem path
                or a Git repository URI pointing to a project directory containing an MLproject file.
    :param entry_point: Entry point to run within the project. If no entry point with the specified
                        name is found, runs the project file ``entry_point`` as a script,
                        using "python" to run ``.py`` files and the default shell (specified by
                        environment variable ``$SHELL``) to run ``.sh`` files.
    :param version: For Git-based projects, either a commit hash or a branch name.
    :param parameters: Parameters (dictionary) for the entry point command.
    :param docker_args: Arguments (dictionary) for the docker command.
    :param experiment_name: Name of experiment under which to launch the run.
    :param backend: Execution backend for the run: W&B provides built-in support for "local",
                    and "ngc" (experimental) backends.
    :param backend_config: A dictionary which will be passed as config to the backend. The exact content
                           which should be provided is different for each execution backend
    :param storage_dir: Used only if ``backend`` is "local". W&B downloads artifacts from
                        distributed URIs passed to parameters of type ``path`` to subdirectories of
                        ``storage_dir``.
    :param synchronous: Whether to block while waiting for a run to complete. Defaults to True.
                        Note that if ``synchronous`` is False and ``backend`` is "local", this
                        method will return, but the current process will block when exiting until
                        the local run completes. If the current process is interrupted, any
                        asynchronous runs launched via this method will be terminated. If
                        ``synchronous`` is True and the run fails, the current process will
                        error out as well.
    :return: :py:class:`wandb.launch.SubmittedRun` exposing information (e.g. run ID)
             about the launched run.
    .. code-block:: python
        :caption: Example
        import wandb
        project_uri = "https://github.com/wandb/examples"
        params = {"alpha": 0.5, "l1_ratio": 0.01}
        # Run W&B project and create a reproducible docker environment
        # on a local host
        wandb.launch(project_uri, parameters=params)
    .. code-block:: text
        :caption: Output
        ...
        ...
        Elasticnet model (alpha=0.500000, l1_ratio=0.010000):
        RMSE: 0.788347345611717
        MAE: 0.6155576449938276
        R2: 0.19729662005412607
        ... wandb.launch: === Run (ID '6a5109febe5e4a549461e149590d0a7c') succeeded ===
    """
    if _is_wandb_local_uri(uri):
        docker_args["network"] = "host"

    submitted_run_obj = _run(
        uri=uri,
        experiment_name=experiment_name,
        wandb_project=wandb_project,
        wandb_entity=wandb_entity,
        docker_image=docker_image,
        entry_point=entry_point,
        version=version,
        parameters=parameters,
        docker_args=docker_args,
        runner_name=resource,
        runner_config=config,
        storage_dir=storage_dir,
        synchronous=synchronous,
        api=api,
    )
    if synchronous:
        _wait_for(submitted_run_obj)
    return submitted_run_obj


def _wait_for(submitted_run_obj):
    """Wait on the passed-in submitted run, reporting its status to the tracking server."""
    # Note: there's a small chance we fail to report the run's status to the tracking server if
    # we're interrupted before we reach the try block below
    try:
        if submitted_run_obj.wait():
            _logger.info("=== Submitted run succeeded ===")
        else:
            raise ExecutionException("Submitted run failed")
    except KeyboardInterrupt:
        _logger.error("=== Submitted run interrupted, cancelling run ===")
        submitted_run_obj.cancel()
        raise
