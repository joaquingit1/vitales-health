"""Pathway identification: which reaction classes apply to an ingredient in a product.

Owner: B2. Contract: docs/ENGINE-SPEC.md section 7.2.
"""

import dataclasses

from pharmacy.chemistry.structure import SiteMatch, mol_from_smiles, site_matches
from pharmacy.errors import ConfigReferenceError
from pharmacy.types import (
    ActiveIngredientDoc,
    PackageDoc,
    ProductDoc,
    ReactionClassDoc,
    ReactionClassesDoc,
    ReactiveSite,
)


@dataclasses.dataclass(frozen=True, slots=True)
class PathwayMatch:
    """A reaction class that applies to an ingredient, with its sites. ``pathway_id`` is
    ``<ingredient_id>:<class_id>``."""

    pathway_id: str
    ingredient_id: str
    reaction_class: ReactionClassDoc
    sites: tuple[ReactiveSite, ...]


def pathway_id(ingredient_id: str, class_id: str) -> str:
    """``<ingredient_id>:<class_id>`` (spec 7.2)."""
    return f"{ingredient_id}:{class_id}"


def package_transmits_light(package: PackageDoc) -> bool:
    """True when the package lets visible or near-UV light through (transmission > 0)."""
    return (
        package["light_transmission_visible"]["value"] > 0.0
        or package["light_transmission_uv"]["value"] > 0.0
    )


def _is_fallback_thermal(reaction_class: ReactionClassDoc) -> bool:
    return (
        not reaction_class["smarts"]
        and reaction_class["driver"] == "thermal"
        and reaction_class["role"] == "destructive"
    )


def _class_applies(
    reaction_class: ReactionClassDoc,
    product: ProductDoc,
    ingredient: ActiveIngredientDoc,
    packages: tuple[PackageDoc, ...],
) -> bool:
    """Every condition of spec 7.2 except having a site."""
    if product["dose_form_family"] not in reaction_class["applies_to_families"]:
        return False
    if reaction_class["driver"] == "light":
        if product["photostable_per_q1b"] is True:
            return False
        if not any(package_transmits_light(package) for package in packages):
            return False
    return not (reaction_class["role"] == "racemization" and ingredient["racemization"] is None)


def _collapse_shared_sites(
    candidates: list[tuple[int, ReactionClassDoc, tuple[SiteMatch, ...]]],
) -> dict[str, tuple[ReactiveSite, ...]]:
    """Two classes of the same mechanism matching the same site atom: the class whose
    matching pattern has more atoms keeps the site, ties go to config order (spec 7.2)."""
    owner: dict[tuple[str, int], tuple[int, int, str]] = {}
    for order, reaction_class, matches in candidates:
        for match in matches:
            key = (reaction_class["mechanism"], match.site.site_atom)
            claim = (-match.pattern_atoms, order, reaction_class["class_id"])
            if key not in owner or claim < owner[key]:
                owner[key] = claim
    kept: dict[str, tuple[ReactiveSite, ...]] = {}
    for _order, reaction_class, matches in candidates:
        class_id = reaction_class["class_id"]
        kept[class_id] = tuple(
            match.site
            for match in matches
            if owner[(reaction_class["mechanism"], match.site.site_atom)][2] == class_id
        )
    return kept


def identify_pathways(
    product: ProductDoc,
    ingredient: ActiveIngredientDoc,
    classes: ReactionClassesDoc,
    packages: tuple[PackageDoc, ...],
) -> tuple[PathwayMatch, ...]:
    """Applicable pathways in reaction-class order (spec 7.2):

    * thermal classes whose SMARTS match the ingredient and whose ``applies_to_families``
      contain the product's dose-form family;
    * the photolysis class only if it matches and ``photostable_per_q1b`` is not true and
      some package in the lot history transmits light (transmission > 0);
    * the radiolysis class always (no SMARTS; driver radiation);
    * racemization only for ingredients with a ``racemization`` block and a match.

    An ingredient with no thermal match gets the class ``unclassified_thermal`` (class
    prior only) so every ingredient has at least one destructive thermal pathway.

    Classes are filtered by these conditions before shared sites collapse, so a class that
    does not apply to the product never takes a site from one that does. The fallback is
    the first class without SMARTS whose driver is thermal and role destructive
    (``unclassified_thermal`` in the configuration); a missing fallback raises
    ``ConfigReferenceError`` when it is needed. ``StructureError`` propagates from an
    unparsable SMILES or SMARTS."""
    mol = mol_from_smiles(ingredient["smiles"])
    ordered = list(classes["classes"])
    candidates: list[tuple[int, ReactionClassDoc, tuple[SiteMatch, ...]]] = []
    for order, reaction_class in enumerate(ordered):
        if not reaction_class["smarts"]:
            continue
        matches = site_matches(mol, reaction_class)
        if matches and _class_applies(reaction_class, product, ingredient, packages):
            candidates.append((order, reaction_class, matches))
    kept = _collapse_shared_sites(candidates)

    selected: dict[str, tuple[ReactiveSite, ...]] = {
        class_id: sites for class_id, sites in kept.items() if sites
    }
    has_thermal_destructive = any(
        reaction_class["driver"] == "thermal"
        and reaction_class["role"] == "destructive"
        and reaction_class["class_id"] in selected
        for reaction_class in ordered
    )
    for reaction_class in ordered:
        if reaction_class["smarts"]:
            continue
        if reaction_class["driver"] == "radiation":
            selected[reaction_class["class_id"]] = ()
    if not has_thermal_destructive:
        fallback = next((c for c in ordered if _is_fallback_thermal(c)), None)
        if fallback is None:
            raise ConfigReferenceError(
                "reaction-classes",
                [f"no fallback thermal class for ingredient {ingredient['ingredient_id']!r}"],
            )
        selected[fallback["class_id"]] = ()

    return tuple(
        PathwayMatch(
            pathway_id=pathway_id(ingredient["ingredient_id"], reaction_class["class_id"]),
            ingredient_id=ingredient["ingredient_id"],
            reaction_class=reaction_class,
            sites=selected[reaction_class["class_id"]],
        )
        for reaction_class in ordered
        if reaction_class["class_id"] in selected
    )
