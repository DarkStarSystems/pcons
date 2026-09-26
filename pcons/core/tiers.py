# SPDX-License-Identifier: MIT
"""Build tiers: which invocation reaches which target.

Every target sits in one of three nested tiers, named by the invocation that
reaches it:

- ``"default"``: plain ``ninja``, and everything below it. The products —
  programs, libraries, commands, documents, packs, whatever this build makes.
- ``"all"``: ``ninja all``, asking for the target itself, and being a
  dependency. The steps that operate on products: installs, overlays,
  archives, installers.
- ``"manual"``: asking for the target itself, only. Test runs (``ninja
  test``, an alias), and targets that must not run unasked: one that
  rewrites sources (Qt's lupdate), one too slow or too destructive for a
  routine build.

Asking for a target is ``pcons build <name>`` when the script named it, and
either way its output path or an alias.

The builder that creates a target places it (:meth:`Target.place_in_tier`);
a script may move one (``bench.build_tier = "all"``); ``Default()`` names the
default tier outright, for the products of its own script's directory and
below; ``add_subdirectory(..., build_tier=...)`` caps a whole included tree. Nothing is decided when those calls happen:
:func:`decide_build_tiers` decides once, at generate, from the final state, so
the answer never depends on the order the build script was written in.

Core knows the three words and nothing about what makes a Program a product;
that is the builder's declaration.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from pcons.core.errors import PconsError

if TYPE_CHECKING:
    from collections.abc import Iterator

    from pcons.core.project import Project
    from pcons.core.target import Target
    from pcons.util.source_location import SourceLocation

#: The tiers, widest invocation first. The only three values `build_tier` takes.
BUILD_TIERS = ("default", "all", "manual")


def validate_tier(tier: str, location: SourceLocation | None = None) -> str:
    """Return *tier* if it is one of :data:`BUILD_TIERS`, else raise."""
    if tier not in BUILD_TIERS:
        choices = ", ".join(f'"{t}"' for t in BUILD_TIERS)
        raise PconsError(
            f"build_tier must be one of {choices}; got {tier!r}",
            location=location,
        )
    return tier


@dataclass(frozen=True, slots=True)
class DefaultCall:
    """Where a ``Default()`` call named a target, and what it governs: the
    products declared in its script's directory and below (*scope*, an
    offset from the top-level root)."""

    location: SourceLocation
    scope: Path


@dataclass(frozen=True, slots=True)
class TierFloor:
    """``add_subdirectory(..., build_tier=...)``: no target declared in that
    subtree sits in a wider tier than *tier*, unless a ``Default()`` from
    outside the subtree names it."""

    tier: str
    location: SourceLocation


def _within(offset: Path, scope: Path) -> bool:
    """True if *offset* is *scope* or below it."""
    return offset.parts[: len(scope.parts)] == scope.parts


@dataclass(frozen=True, slots=True)
class TierDecision:
    """One target's decided tier, why, and where that was decided.

    ``location`` is the build-script line responsible, when one is: the
    ``build_tier`` assignment or the ``Default()`` call. A tier the builder
    placed has no script line.
    """

    target: Target
    tier: str
    reason: str
    location: SourceLocation | None = None


class BuildTiers:
    """Every target's decided tier: the result of :func:`decide_build_tiers`.

    Reads as a mapping from target to :class:`TierDecision`, in project-tree
    order, plus the two target sets the generators write:
    :attr:`default_targets` (plain ``ninja``) and :attr:`all_targets`
    (``ninja all``).
    """

    def __init__(self, project: Project, decisions: list[TierDecision]) -> None:
        self._project = project
        self._decisions = decisions
        # By identity: a target's name, and so its hash, is still the
        # script's to change right up to generate.
        self._by_target = {id(decision.target): decision for decision in decisions}

    def __iter__(self) -> Iterator[TierDecision]:
        return iter(self._decisions)

    def __len__(self) -> int:
        return len(self._decisions)

    def __contains__(self, target: Target) -> bool:
        return id(target) in self._by_target

    def __getitem__(self, target: Target) -> TierDecision:
        return self._by_target[id(target)]

    def get(self, target: Target) -> TierDecision | None:
        """This target's decision, or None if it is not in the tree."""
        return self._by_target.get(id(target))

    @property
    def default_targets(self) -> list[Target]:
        """The targets plain ``ninja`` builds."""
        return [d.target for d in self._decisions if d.tier == "default"]

    @property
    def all_targets(self) -> list[Target]:
        """The targets ``ninja all`` builds: everything but the manual ones."""
        return [d.target for d in self._decisions if d.tier != "manual"]

    def report_lines(self, targets: list[Target] | None = None) -> list[str]:
        """The "build tiers" report: a header, then one section per tier.

        Used by ``pcons explain`` and by ``-v`` at generate, so both say the
        same thing. Tiers come widest invocation first (default, all,
        manual); within a tier, targets are listed by subdirectory and then
        by name, so a reader finds a target where they expect it rather
        than where the script happened to declare it. *targets* restricts
        the report to those targets; by default every target is reported.
        """
        decisions = (
            [d for t in targets if (d := self.get(t)) is not None]
            if targets is not None
            else self._decisions
        )
        if not decisions:
            return []
        # The subdirectory column appears only when there is something to
        # tell: a single-directory project has no use for a blank column.
        subdirs = {id(d): _subdir_text(d.target) for d in decisions}
        subdir_width = max(len(s) for s in subdirs.values())
        name_width = max(len(d.target.name) for d in decisions)
        reason_width = max(len(d.reason) for d in decisions)
        lines = ["build tiers:"]
        for tier in BUILD_TIERS:
            members = sorted((d for d in decisions if d.tier == tier), key=_listing_key)
            if not members:
                continue
            lines.append(f"  {tier}:")
            for d in members:
                line = f"    {d.target.name:<{name_width}}  "
                if subdir_width:
                    line += f"{subdirs[id(d)]:<{subdir_width}}  "
                line += f"{d.reason:<{reason_width}}"
                if d.location is not None:
                    line += f"  {self._where(d.location)}"
                lines.append(line.rstrip())
        return lines

    def _where(self, location: SourceLocation) -> str:
        """*location* as ``file:line``, relative to the project root when it
        is under it (a build script usually is; pcons's own files are not)."""
        filename = Path(location.filename)
        root = self._project.root_dir.absolute()
        if filename.is_absolute() and filename.is_relative_to(root):
            filename = filename.relative_to(root)
        return f"{filename.as_posix()}:{location.lineno}"


def _subdir_text(target: Target) -> str:
    """The subdirectory a target was declared in, empty at the top level."""
    return target._subdir.as_posix() if target._subdir.parts else ""


def _listing_key(decision: TierDecision) -> tuple[tuple[str, ...], str, str]:
    """Sort key for the report: subdirectory, then name (case-insensitive,
    with the exact name breaking ties so the order is stable)."""
    subdir = tuple(part.casefold() for part in decision.target._subdir.parts)
    name = decision.target.name
    return (subdir, name.casefold(), name)


def decide_build_tiers(project: Project) -> BuildTiers:
    """Decide every target's tier, once, from the whole tree's final state.

    For each target, the first rule that applies:

    1. The script set ``build_tier``: that value.
    2. ``Default()`` named it: ``"default"``.
    3. A ``Default()`` call governs it, from its own script's directory or
       one above: ``"all"`` if its builder placed it in ``"default"``
       (naming defaults demotes the unnamed products), else the builder's
       value (a step or a manual target is untouched).
    4. The builder's placement.

    Then a floor: under ``add_subdirectory(..., build_tier=...)`` the target
    goes no wider than that tier, unless a ``Default()`` from outside the
    subtree named it. The deepest such subdirectory decides.

    Raises:
        PconsError: A target is both named in ``Default()`` and set by the
            script to ``"all"`` or ``"manual"``, which cannot both be meant.
            The message names both lines.
    """
    named: dict[int, DefaultCall] = {}
    for node in project._iter_tree():
        named.update(node._default_at)
    # Each call's scope, with the line a reader looks for first: the first
    # call to govern it.
    scopes: dict[Path, SourceLocation] = {}
    for call in named.values():
        scopes.setdefault(call.scope, call.location)
    floors = project.top._tier_floors

    decisions: list[TierDecision] = []
    for target in project.targets:
        decision = _decide_one(target, named.get(id(target)), scopes)
        floor_at = max(
            (offset for offset in floors if _within(target._subdir, offset)),
            key=lambda offset: len(offset.parts),
            default=None,
        )
        if floor_at is not None:
            floor = floors[floor_at]
            call = named.get(id(target))
            lifted = call is not None and not _within(call.scope, floor_at)
            deeper = BUILD_TIERS.index(floor.tier) > BUILD_TIERS.index(decision.tier)
            if deeper and not lifted:
                decision = TierDecision(
                    target,
                    floor.tier,
                    f'add_subdirectory(..., build_tier="{floor.tier}")',
                    floor.location,
                )
        decisions.append(decision)
    return BuildTiers(project, decisions)


def _decide_one(
    target: Target,
    call: DefaultCall | None,
    scopes: dict[Path, SourceLocation],
) -> TierDecision:
    """Rules 1-4 of :func:`decide_build_tiers` for one target."""
    chosen_at = target._build_tier_at
    if chosen_at is not None:
        if call is not None and target.build_tier != "default":
            raise PconsError(
                f"target '{target.name}' is named in Default() at "
                f"{call.location}, and set to build_tier = "
                f'"{target.build_tier}" at {chosen_at}. '
                "A default target is in the default tier, so these "
                "contradict: drop one.",
                location=chosen_at,
            )
        return TierDecision(
            target, target.build_tier, f'build_tier = "{target.build_tier}"', chosen_at
        )
    if call is not None:
        return TierDecision(target, "default", "named in Default()", call.location)
    if target.build_tier == "default":
        governing = next(
            (at for scope, at in scopes.items() if _within(target._subdir, scope)),
            None,
        )
        if governing is not None:
            return TierDecision(target, "all", "not named in Default()", governing)
    return TierDecision(target, target.build_tier, _placement_reason(target))


def _placement_reason(target: Target) -> str:
    """Why a target sits where its builder put it, naming the builder."""
    by = target._build_tier_by or target._builder_name
    tier = target.build_tier
    if tier == "manual":
        return f"placed by {by}" if by else "placed manually"
    word = "product" if tier == "default" else "step"
    return f"{word} ({by})" if by else word
