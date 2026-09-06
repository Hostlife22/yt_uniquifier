from pathlib import Path

import yaml


def test_manual_docker_qualification_can_skip_registry_mutation() -> None:
    path = Path(__file__).parents[2] / ".github/workflows/docker.yml"
    workflow = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
    publish = workflow["on"]["workflow_dispatch"]["inputs"]["publish"]
    assert publish["type"] == "boolean"
    assert publish["default"] == "true"  # retain existing publication behavior
    assert "if" not in workflow["jobs"]["smoke"]
    assert workflow["jobs"]["buildx"]["needs"] == "smoke"
    assert workflow["jobs"]["buildx"]["if"] == (
        "github.event_name != 'workflow_dispatch' || inputs.publish"
    )
