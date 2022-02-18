#!/usr/bin/env python3
import os
import pathlib
import re
import shutil
from xml.etree import ElementTree

import rospkg

COLLADA_NS = "{http://www.collada.org/2005/11/COLLADASchema}"

SIMULATION_ASSETS_FOLDER = pathlib.Path(__file__).parents[1] / "simulation_files"
VMF_FOLDERS_TO_COPY = [
    "Media",
    "map",
    "worlds",
    "launch",
]  # Folders that are needed by the simulation container
GZWEB_VMF_FOLDERS_TO_COPY = [
    "models",
    "Media/models",
]  # Folders that are needed for GZWeb in the simulation container
GZWEB_EXTENSIONS_TO_KEEP = [
    ".stl",
    ".dae",
    ".sdf",
    ".config",
    ".material",
    ".png",
    ".jpg",
    ".tiff",
    ".jpeg",
    ".gazebo",
]  # File extensions that are needed by GZWeb

GAZEBO_EXTENSIONS_TO_KEEP = [
    ".stl",
    ".dae",
    ".sdf",
    ".config",
    ".material",
    ".png",
    ".jpg",
    ".tiff",
    ".jpeg",
    ".gazebo",
    ".xml",
]  # File extensions that are needed by Gazebo in the simulation container


def get_workspace_folder():
    workspace_src_folder = pathlib.Path(
        rospkg.RosPack().get_path("virtual_maize_field")
    )

    # Try to resolve the 'src' folder recursively
    max_depth = 10
    current_depth = 0
    while workspace_src_folder.name != "src" and current_depth < max_depth:
        workspace_src_folder = workspace_src_folder.parent
        current_depth += 1

    if current_depth == max_depth:
        raise NotADirectoryError("Cannot resolve the workspace source folder.")

    return workspace_src_folder


def get_gazebo_material_resources():
    gz_resource_path = os.environ.get("GAZEBO_RESOURCE_PATH", None)

    if gz_resource_path is None:
        raise EnvironmentError("GAZEBO_RESOURCE_PATH is not set!")

    for gz_resource in gz_resource_path.split(":"):
        material_resource_folder = (
            pathlib.Path(gz_resource).resolve() / "media/materials/scripts"
        )
        if material_resource_folder.is_dir():
            return material_resource_folder

    raise NotADirectoryError("Could not find the gazebo material resource folder!")


def get_simulation_resources_from_file(file):
    packages = []
    resources = []

    root = ElementTree.parse(file).getroot()
    for tag in root.findall(".//*[@filename]"):
        if tag.attrib["filename"].startswith("package://"):
            package_name_match = re.search(
                r"package:\/\/(.+?)\/(.+\..+)", tag.attrib["filename"]
            )
            if package_name_match is None:
                raise SyntaxError(
                    f"Cannot match resource path {tag.attrib['filename']}"
                )

            package_name, resource_path = package_name_match.group(
                1
            ), package_name_match.group(2)

        elif tag.attrib["filename"].startswith("$(find"):
            package_name_match = re.search(
                r"\$\(find (.+)\)\/(.+\..+)", tag.attrib["filename"]
            )
            if package_name_match is None:
                raise SyntaxError(
                    f"Cannot match resource path {tag.attrib['filename']}"
                )

            package_name, resource_path = package_name_match.group(
                1
            ), package_name_match.group(2)

        elif tag.attrib["filename"].endswith(".so"):
            continue

        else:
            raise NotImplementedError(f"Cannot parse {tag.attrib['filename']}")

        # Resolve file path
        resource_path_in_package = (
            resource_path[1:] if resource_path[0] == "/" else resource_path
        )
        resource_path = (
            pathlib.Path(rospkg.RosPack().get_path(package_name))
            / resource_path_in_package
        )

        if not resource_path.is_file():
            raise FileNotFoundError(
                f"Could not resolve file {resource_path} in {tag.attrib['filename']}"
            )

        packages.append(package_name)
        resources.append(resource_path)

    return packages, resources


def get_simulation_resources_from_workspace():
    dependencies = []
    workspace = get_workspace_folder()

    XACRO_IN_LAUNCH_REGEX = re.compile(r"\$\(find\s(\S+)\)(\S+\.xacro|\.urdf)")

    # Loop over all launch files in workspace and gather all .urdf and .xacro files that are used within these
    # launch files.
    used_xacro_files = []
    for launch_file in workspace.glob("**/*.launch"):
        for use_of_xacro in re.finditer(
            XACRO_IN_LAUNCH_REGEX, launch_file.read_text(encoding="utf-8")
        ):
            package_name, file_path = use_of_xacro.group(1), use_of_xacro.group(2)

            # Resolve file path
            xacro_path = (
                pathlib.Path(rospkg.RosPack().get_path(package_name)) / file_path[1:]
            )

            if not xacro_path.is_file():
                raise FileNotFoundError(
                    f"Could not resolve file {xacro_path} in {launch_file}"
                )

            used_xacro_files.append(xacro_path)

    # Parse dependencies of the xacro files recursively, following the .xacro files used within the collected
    # .xacro files
    dependency_contains_xacro = True
    while dependency_contains_xacro:
        xacro_resource_paths = []

        for xacro_file in used_xacro_files:
            packages, resouce_paths_in_file = get_simulation_resources_from_file(
                xacro_file
            )
            xacro_resource_paths.extend(resouce_paths_in_file)

            # Add discovered packages to the dependencies
            dependencies.extend(packages)

        # Decide if we should go a level deeper to recursively parse the .xacro dependencies of these files
        # as well.
        if len(xacro_resource_paths) == 0:
            dependency_contains_xacro = False

        elif not any(
            r.suffix == ".xacro" or r.suffix == ".urdf" for r in xacro_resource_paths
        ):
            dependency_contains_xacro = False

        else:
            dependency_contains_xacro = True
            used_xacro_files = [
                r
                for r in xacro_resource_paths
                if r.suffix == ".xacro" or r.suffix == ".urdf"
            ]

    return list(set(dependencies))


class Validation:
    ERROR = 1
    OK = 2
    WARNING = 3

    def __init__(self):
        self.validation_checks = []

    def register(self, f):
        self.validation_checks.append(f)

    def validate_all(self):
        for ck in self.validation_checks:
            name = ck.__name__.replace("check_", "").replace("_", " ").capitalize()
            val, msg = ck()

            if val == Validation.ERROR:
                color = "\033[91m"
                sign = "\u2718"
            elif val == Validation.WARNING:
                color = "\033[93m"
                sign = "\u003f"
            else:
                sign = "\u2714"
                color = "\033[92m"

            print(f"{color}{sign} {name : <20}\t: {msg}\033[0m")
            if val == Validation.ERROR:
                return False

        return True


# Create validator to check the folder structure, meshes etc.
validator = Validation()


@validator.register
def check_find_workspace():
    # Check if we can find the workspace based on the ROS PACKAGE PATH of the virtual_maize_field package
    try:
        workspace_src_folder = get_workspace_folder()

        msg = f"Found workspace source folder in '{workspace_src_folder}'"
        return Validation.OK, msg

    except NotADirectoryError:
        msg = (
            "Cannot find your workspace 'src' folder. Did you place the"
            " 'virtual_maize_field' package in your 'src' folder?"
        )
        return Validation.ERROR, msg

    except rospkg.common.ResourceNotFound:
        msg = (
            "Cannot find package 'virtual_maize_field'. Did you clone the virtual maize"
            " field package into your workspace and did a 'catkin_make'? Did you source"
            " your workspace?"
        )
        return Validation.ERROR, msg


@validator.register
def check_find_gazebo_resources():
    # Check if we can find the Gazebo material resource
    try:
        gazebo_material_resource_folder = get_gazebo_material_resources()

        msg = (
            "Found gazebo material resource folder in"
            f" '{gazebo_material_resource_folder}'"
        )
        return Validation.OK, msg

    except NotADirectoryError:
        msg = (
            "Could not find the gazebo material resource folder! Check your Gazebo"
            " installation."
        )
        return Validation.ERROR, msg

    except EnvironmentError:
        msg = (
            "GAZEBO_RESOURCE_PATH is not set! Did you source Gazebo in your .bashrc"
            " file? If not, try adding 'source /usr/share/gazebo/setup.bash' to your"
            " bash file."
        )
        return Validation.ERROR, msg


@validator.register
def check_world_file():
    world_file = (
        pathlib.Path(rospkg.RosPack().get_path("virtual_maize_field"))
        / "worlds/generated.world"
    )

    if not world_file.is_file():
        msg = (
            f"Could not find your generated .world file at '{world_file}'. Generate a"
            " world file by 'rosrun virtual_maize_field create_task_1_mini'"
        )
        return Validation.ERROR, msg

    # Empty <materials></materials> tags don't work using GZWeb. Raise error if they are in the world file. Probably because of old world
    # template
    root = ElementTree.parse(world_file).getroot()
    for m_tag in root.findall(".//materials"):
        if m_tag.text is None:
            msg = (
                f"The world file '{world_file}' contains empty <materials></materials>"
                " tags. This gives a problem in visualisation of the environment. Update"
                " the virtual_maize_field package to the newest version or manually remove"
                " the empty <materials></materials>tags from the world file."
            )
            return Validation.ERROR, msg

    msg = "World file is correct."
    return Validation.OK, msg


@validator.register
def check_xacro_dependencies():
    try:
        resources = get_simulation_resources_from_workspace()
        msg = f"Need resources from {resources}"
        return Validation.OK, msg

    except FileNotFoundError as exc:
        msg = (
            "Could not find all dependencies of the xacro and urdf files:"
            f" '{str(exc)}'."
        )
        return Validation.WARNING, msg

    except SyntaxError as exc:
        msg = f"Syntax of some files is not correct: '{str(exc)}'."
        return Validation.WARNING, msg

    except rospkg.common.ResourceNotFound as exc:
        msg = (
            "Could not find all packages required by the xacro and urdf files:"
            f" '{str(exc)}'."
        )
        return Validation.WARNING, msg


@validator.register
def check_mesh_files():
    workspace_src_folder = get_workspace_folder()

    i = 0
    for mesh_file in workspace_src_folder.glob("**/*.dae"):
        i += 1

        root = ElementTree.parse(mesh_file).getroot()
        for init_tag in root.findall(f".//{COLLADA_NS}init_from"):
            if (
                init_tag.text.endswith(".png")
                or init_tag.text.endswith(".jpg")
                or init_tag.text.endswith(".jpeg")
            ) and "../materials/textures" not in init_tag.text:
                msg = (
                    f"Texture '{ init_tag.text.split('/')[-1] }' in"
                    f" '{mesh_file.parents[1]}' should be placed in the folder"
                    f" {mesh_file.parents[1]}/materials/textures'. Move the file to this"
                    f" folder and edit the '{mesh_file.name} file."
                )
                return Validation.ERROR, msg

    msg = f"All { i } mesh files are valid"
    return Validation.OK, msg


def copytree(src, dst, symlinks=False, ignore=None):
    """
    Source: https://stackoverflow.com/questions/1868714/how-do-i-copy-an-entire-directory-of-files-into-an-existing-directory-using-pyth
    """
    if not os.path.exists(dst):
        os.makedirs(dst)
    for item in os.listdir(src):
        s = os.path.join(src, item)
        d = os.path.join(dst, item)
        if os.path.isdir(s):
            copytree(s, d, symlinks, ignore)
        else:
            if not os.path.exists(d) or os.stat(s).st_mtime - os.stat(d).st_mtime > 1:
                shutil.copy2(s, d)


def remove_empty_directories(path, remove_root=True):
    """
    Source: https://jacobtomlinson.dev/posts/2014/python-script-recursively-remove-empty-folders/directories/
    """
    if not os.path.isdir(path):
        return

    # remove empty subfolders
    files = os.listdir(path)
    if len(files):
        for f in files:
            full_path = os.path.join(path, f)
            if os.path.isdir(full_path):
                remove_empty_directories(full_path)

    # if folder empty, delete it
    files = os.listdir(path)
    if len(files) == 0 and remove_root:
        os.rmdir(path)


def gather_and_copy_files():
    vmf = pathlib.Path(rospkg.RosPack().get_path("virtual_maize_field"))

    for folder in VMF_FOLDERS_TO_COPY:
        print(f"\033[92m\u2714 {folder} -> {SIMULATION_ASSETS_FOLDER / folder}\033[0m")
        copytree(vmf / folder, SIMULATION_ASSETS_FOLDER / folder)

    # Create gzweb assets
    gzweb_folder = SIMULATION_ASSETS_FOLDER / "gzweb"
    gzweb_folder.mkdir()

    for folder in GZWEB_VMF_FOLDERS_TO_COPY:
        print(f"\033[92m\u2714 {folder} -> {gzweb_folder}\033[0m")
        copytree(vmf / folder, gzweb_folder)

    # Copy additional Gazebo resources
    gazebo_material_resources = get_gazebo_material_resources()
    print(
        f"\033[92m\u2714 {gazebo_material_resources} ->"
        f" {gzweb_folder}/materials/scripts\033[0m"
    )
    copytree(gazebo_material_resources, gzweb_folder / "materials/scripts")

    # Copy all custom packages from workspace
    required_packages = get_simulation_resources_from_workspace()
    for pkg in required_packages:
        pkg_path = pathlib.Path(rospkg.RosPack().get_path(pkg))
        if (pkg_path / "meshes").is_dir():
            print(f"\033[92m\u2714 {pkg_path} -> {gzweb_folder}\033[0m")
            copytree(pkg_path, gzweb_folder / pkg_path.name)

    # Cleanup GZWeb assets folder
    for f in gzweb_folder.glob("**/*"):
        if f.is_file() and not f.suffix.lower() in GZWEB_EXTENSIONS_TO_KEEP:
            f.unlink()
    remove_empty_directories(gzweb_folder)

    # Copy all custom packages from workspace to robot packages folder, they are needed to start Gazebo
    robot_packages_folder = SIMULATION_ASSETS_FOLDER / "robot_packages"
    robot_packages_folder.mkdir()

    required_packages = get_simulation_resources_from_workspace()
    for pkg in required_packages:
        pkg_path = pathlib.Path(rospkg.RosPack().get_path(pkg))
        print(f"\033[92m\u2714 {pkg_path} -> {robot_packages_folder}\033[0m")
        copytree(pkg_path, robot_packages_folder / pkg_path.name)

    # Cleanup folder
    for f in robot_packages_folder.glob("**/*"):
        if f.is_file() and not f.suffix.lower() in GAZEBO_EXTENSIONS_TO_KEEP:
            f.unlink()
    remove_empty_directories(robot_packages_folder)


if __name__ == "__main__":
    # Check if current workspace is correct
    print("Check if workspace is correct:")
    valid = validator.validate_all()

    if valid:
        print("\nRemoving old simulation files...")
        for f in SIMULATION_ASSETS_FOLDER.glob("*"):
            if f.is_dir():
                shutil.rmtree(f)

        print("\nCopy files:")
        gather_and_copy_files()
