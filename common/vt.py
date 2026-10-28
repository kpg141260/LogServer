__VERSION__ = "0.8.0.5"
# <VT_VERSION_OBJECT_START>
import json
from pathlib import Path

__VT_FILE__ = "common/vt.py"
__VT_MANIFEST__ = ".vt/version.manifest"


class VersionInfo:
    def __init__(self, manifest_name=__VT_MANIFEST__, file_name=__VT_FILE__):
        self.manifest_name = manifest_name
        self.file_name = self._normalize_name(file_name)
        self.data = self._load()

    def _load(self):
        folder = Path(__file__).parent if "__file__" in globals() else Path(".")
        while True:
            path = folder / self.manifest_name
            if path.is_file():
                with path.open("r") as f:
                    return json.load(f)

            parent = folder.parent
            if parent == folder:
                return {}
            folder = parent

    def _normalize_name(self, name) -> str:
        return str(name).replace("\\", "/").lstrip("/\\")

    @property
    def project_name(self) -> str:
        return self.data['project']['name']

    @property
    def project_info(self) -> dict:
        return self.data.get("project", {})

    @property
    def project_version(self) -> str:
        return f'{self.project_name} {self.version()}'

    @property
    def build_info(self) -> dict:
        return self.project_info.get("build_info", {})

    @property
    def file(self) -> dict:
        return self.data.get(self.file_name, {})

    def _version_string(self, build=None) -> str:
        b = self.build_info
        if build is None:
            build = b.get("build", 0)
        return f"{b.get('major', 0)}.{b.get('minor', 0)}.{b.get('patch', 0)}.{build}"

    # The main version function
    def version(self, file_name=None) -> str | dict:
        """
        Return version information from the manifest.

        version()              -> project version string
        version("all")         -> dict of tracked file versions
        version(all)           -> same as version("all")
        version("path/file.py") -> version string for one tracked file, or "not found"
        """
        if file_name is None:
            return self._version_string()

        if str(file_name).lower() == "all":
            return {
                key: self.data[key].get('version') or self._version_string(self.data[key].get("build", 0))
                for key in sorted(
                    (k for k in self.data if k != "project"),
                    key=str.lower,
                )
            }

        key = self._normalize_name(file_name)
        item = self.data.get(key)
        if item is None:
            return "not found"

        return item.get('version') or self._version_string(item.get("build", 0))

__version_info__ = VersionInfo()
__VERSION__ = __version_info__.version()
# <VT_VERSION_OBJECT_END>
