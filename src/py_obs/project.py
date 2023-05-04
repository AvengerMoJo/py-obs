import asyncio
from dataclasses import dataclass, field
from typing import ClassVar, overload
import xml.etree.ElementTree as ET

from py_obs.osc import Osc
from py_obs.person import (
    OwnerCollection,
    Person,
    Person2,
    PersonRole,
    UserGroup,
    fetch_group,
)
from .xml_factory import MetaMixin, StrElementField


@dataclass(frozen=True)
class PathEntry(MetaMixin):
    project: str
    repository: str

    _element_name: ClassVar[str] = "path"


@dataclass(frozen=True)
class Repository(MetaMixin):
    name: str
    path: list[PathEntry] | None = None
    arch: list[str] | None = None

    _element_name: ClassVar[str] = "repository"


@dataclass(frozen=True)
class Project(MetaMixin):
    name: str
    title: StrElementField
    description: StrElementField = StrElementField("")

    person: list[Person] | None = None
    repository: list[Repository] | None = None

    _element_name: ClassVar[str] = "project"


@dataclass(frozen=True)
class Package:
    name: str
    title: str
    description: str = ""

    scmsync: str | None = None

    _element_name: ClassVar[str] = "package"

    @property
    def meta(self) -> ET.Element:
        (pkg_conf := ET.Element(Package._element_name)).attrib["name"] = self.name
        (title := ET.Element("title")).text = self.title
        (descr := ET.Element("description")).text = self.description
        pkg_conf.append(title)
        pkg_conf.append(descr)
        if self.scmsync:
            (scmsync := ET.Element("scmsync")).text = self.scmsync
            pkg_conf.append(scmsync)

        return pkg_conf


@dataclass(frozen=True)
class PackageMaintainers:
    package: list[Person2] = field(default_factory=list)
    project: list[Person2] = field(default_factory=list)


@overload
async def search_for_maintainers(
    osc: Osc,
    *,
    pkg: Package,
    roles: list[PersonRole] | None = None,
    groups_to_ignore: list[str] | None = None,
) -> PackageMaintainers:
    ...


@overload
async def search_for_maintainers(
    osc: Osc,
    *,
    pkg_name: str,
    roles: list[PersonRole] | None = None,
    groups_to_ignore: list[str] | None = None,
) -> PackageMaintainers:
    ...


async def search_for_maintainers(
    osc: Osc,
    *,
    pkg: Package | None = None,
    pkg_name: str | None = None,
    roles: list[PersonRole] | None = None,
    groups_to_ignore: list[str] | None = None,
) -> PackageMaintainers:
    """Query the build service to find the maintainers of the package provided
    either by name or via a :py:class:`Package` instance.

    This function includes the maintainers from groups in the result.
    You can exclude the members from specific groups to be added to the results
    by adding the groupname to the ``groups_to_ignore`` parameter. This can be
    used to exclude e.g. ``factory-maintainers`` who are listed as
    co-maintainers for every package in ``openSUSE:Factory``.
    """

    if not pkg_name:
        assert pkg
        pkg_name = pkg.name

    if groups_to_ignore is None:
        groups_to_ignore = []

    params = {"package": pkg_name}
    if roles:
        params["filter"] = ",".join(roles)

    owners = await OwnerCollection.from_response(
        await osc.api_request("/search/owner", method="GET", params=params)
    )

    pkg_maintainers = []
    prj_maintainers = []
    for owner in owners.owner:

        async def fetch_group_members() -> list[Person2]:
            tasks = []
            for grp in owner.group:
                if grp.name not in groups_to_ignore:
                    tasks.append(fetch_group(osc, grp.name))

            res: tuple[UserGroup] = await asyncio.gather(*tasks)
            return [
                Person2(maint.userid) for grp in res for maint in grp.maintainer
            ] + [Person2(pers.userid) for grp in res for pers in grp.person.person]

        if owner.project and owner.package == pkg_name:
            pkg_maintainers.extend(owner.person)
            pkg_maintainers.extend(await fetch_group_members())

        if owner.project and not owner.package:
            prj_maintainers.extend(owner.person)
            prj_maintainers.extend(await fetch_group_members())

    return PackageMaintainers(
        package=list(set(pkg_maintainers)), project=list(set(prj_maintainers))
    )


@overload
async def send_meta(osc: Osc, *, prj: Project) -> None:
    ...


@overload
async def send_meta(osc: Osc, *, prj: Project, pkg: Package) -> None:
    ...


@overload
async def send_meta(osc: Osc, *, prj_name: str, prj_meta: ET.Element) -> None:
    ...


@overload
async def send_meta(
    osc: Osc, *, prj_name: str, pkg_name: str, pkg_meta: ET.Element
) -> None:
    ...


async def send_meta(
    osc: Osc,
    *,
    prj: Project | None = None,
    prj_name: str | None = None,
    prj_meta: ET.Element | None = None,
    pkg: Package | None = None,
    pkg_name: str | None = None,
    pkg_meta: ET.Element | None = None,
) -> None:
    route = "/source/"

    if prj and pkg:
        route += f"{prj.name}/{pkg.name}"
        meta = pkg.meta
    elif prj and not pkg:
        route += prj.name
        meta = prj.meta
    elif prj_name and pkg_name and pkg_meta:
        route += f"{prj_name}/{pkg_name}"
        meta = pkg_meta
    elif prj_name and prj_meta:
        route += prj_name
        meta = prj_meta
    else:
        assert False, "Invalid parameter combination"

    route += "/_meta"

    await osc.api_request(route=route, payload=ET.tostring(meta), method="PUT")


@overload
async def delete(osc: Osc, *, prj: Project | str, force: bool = False) -> None:
    ...


@overload
async def delete(
    osc: Osc, *, prj: Project | str, pkg: Package | str, force: bool = False
) -> None:
    ...


async def delete(
    osc: Osc,
    *,
    prj: Project | str,
    pkg: Package | str | None = None,
    force: bool = False,
) -> None:
    prj_name = prj.name if isinstance(prj, Project) else prj
    route = f"/source/{prj_name}/"
    if pkg:
        route += pkg.name if isinstance(pkg, Package) else pkg

    await osc.api_request(
        route, method="DELETE", params={"force": "1"} if force else None
    )


@dataclass(frozen=True)
class _Directory(MetaMixin):
    @dataclass(frozen=True)
    class Entry(MetaMixin):
        _element_name: ClassVar[str] = "entry"

        name: str | None
        md5: str | None
        size: int | None
        mtime: int | None
        originproject: str | None
        available: bool | None
        recommended: bool | None
        hash: str | None

    @dataclass(frozen=True)
    class LinkInfo(MetaMixin):
        _element_name: ClassVar[str] = "linkinfo"

        project: str | None
        package: str | None
        srcmd5: str | None
        rev: str | None
        baserev: str | None
        xsrcmd5: str | None
        lsrcmd5: str | None
        error: str | None

    @dataclass(frozen=True)
    class ServiceInfo(MetaMixin):
        _element_name: ClassVar[str] = "serviceinfo"
        code: str | None
        error: str | None
        lsrcmd5: str | None
        xsrcmd5: str | None

    _element_name: ClassVar[str] = "directory"

    name: str | None
    rev: str | None
    vrev: str | None
    srcmd5: str | None
    count: int | None

    entry: list[Entry]
    linkinfo: list[LinkInfo]
    serviceinfo: list[ServiceInfo]


def _prj_and_pkg_name(prj: str | Project, pkg: Package | str) -> tuple[str, str]:
    return (
        prj.name if isinstance(prj, Project) else prj,
        pkg.name if isinstance(pkg, Package) else pkg,
    )


@dataclass(frozen=True)
class File:
    #: The file name
    name: str

    #: MD5 Hash of the file contents
    md5_sum: str

    #: file size in bytes
    size: int

    #: Unix time of the last modification
    mtime: int


async def fetch_file_list(
    osc: Osc, prj: str | Project, pkg: Package | str
) -> list[File]:
    """Fetch the list of files of a package in the given project."""
    prj_name, pkg_name = _prj_and_pkg_name(prj, pkg)

    return [
        File(name=entry.name, md5_sum=entry.md5, size=entry.size, mtime=entry.mtime)
        for entry in (
            await _Directory.from_response(
                await osc.api_request(route=f"/source/{prj_name}/{pkg_name}")
            )
        ).entry
        if entry.name and entry.md5 and entry.size and entry.mtime
    ]
