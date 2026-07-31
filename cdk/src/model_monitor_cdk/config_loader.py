"""Config loader — YAML → JSON serialization for v2 system."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from model_monitor_cdk.config import ProjectSpec, ProjectsConfig


@dataclass
class ProjectConfigVersion:
    """Versioned project config reference for S3."""

    project_name: str
    version: int

    @property
    def s3_key(self) -> str:
        """Generate versioned S3 object key."""
        return f"{self.project_name}/v{self.version}/config.json"


class ConfigLoader:
    """Load project YAML and serialize to JSON for S3 versioning.

    Attributes:
        projects: Parsed ProjectsConfig from YAML.
    """

    def __init__(self, projects_yaml_path: Path):
        """Load and validate projects.yaml.

        Args:
            projects_yaml_path: Path to projects.yaml file.

        Raises:
            FileNotFoundError: If the YAML file does not exist.
            ValueError: If the YAML is malformed or fails validation.
        """
        if not projects_yaml_path.exists():
            msg = f"projects.yaml not found: {projects_yaml_path}"
            raise FileNotFoundError(msg)

        import yaml

        try:
            yaml_data = yaml.safe_load(projects_yaml_path.read_text(encoding="utf-8"))
        except Exception as exc:
            msg = f"Failed to parse projects.yaml: {exc}"
            raise ValueError(msg) from exc

        # Validate against ProjectsConfig schema
        try:
            self.projects = ProjectsConfig(**yaml_data)
        except Exception as exc:
            msg = f"projects.yaml failed validation: {exc}"
            raise ValueError(msg) from exc

    def to_json(self) -> str:
        """Serialize full projects config to JSON.

        Returns:
            JSON string of all projects.
        """
        data = {"projects": [p.model_dump() for p in self.projects.projects]}
        return json.dumps(data, indent=2)

    def to_json_for_project(self, project_name: str) -> str:
        """Serialize config for one project to JSON.

        Args:
            project_name: Name of the project to extract.

        Returns:
            JSON string of the project config.

        Raises:
            ValueError: If project not found.
        """
        for project in self.projects.projects:
            if project.name == project_name:
                return json.dumps(project.model_dump(), indent=2)
        msg = f"Project {project_name!r} not found in projects.yaml"
        raise ValueError(msg)

    @staticmethod
    def s3_key_for_project(project_name: str, version: int) -> str:
        """Generate versioned S3 object key for a project.

        Args:
            project_name: Project name.
            version: Config version number.

        Returns:
            S3 object key (e.g., "project-a/v1/config.json").
        """
        return f"{project_name}/v{version}/config.json"
