"""Bulk FHIR R5 data generator built on `fhir.resources` + `Faker`.

How it scales
-------------
The :data:`PLAN` dict maps each FHIR resource type to a ``count_at_scale_1``.
``--scale 1.0`` produces roughly the counts in PLAN (≈70k rows). ``--scale 0.1``
produces ~7k rows; ``--scale 10`` produces ~700k. Every count is multiplied by
the scale factor and rounded up (so even small fractional scales still emit at
least one of every type the user asked for).

Reproducibility
---------------
A single integer ``seed`` controls both Faker and the stdlib ``random.Random``
instance, so two runs with the same ``--seed`` produce byte-identical resources.
Resource ids are formatted like ``bulk-patient-000123`` (six-digit zero-padded
serial), so re-running with the same seed against an already-populated DB is a
no-op thanks to ``INSERT...ON CONFLICT DO NOTHING`` in
:meth:`app.store.FHIRStore.bulk_create`.

Reference graph
---------------
Generation runs in dependency order (see :func:`generate`). The
:class:`BulkContext` records every generated id by type, and downstream
generators sample those lists when they need a reference (e.g. an Observation
picks a random Patient, a Slot picks a random Schedule). This keeps every
reference resolvable without forcing the caller to maintain ordering by hand.
"""

from __future__ import annotations

import base64
import math
import random
from collections import defaultdict
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from faker import Faker
from fhir.resources.address import Address
from fhir.resources.allergyintolerance import AllergyIntolerance
from fhir.resources.annotation import Annotation
from fhir.resources.appointment import Appointment, AppointmentParticipant
from fhir.resources.attachment import Attachment
from fhir.resources.careplan import CarePlan
from fhir.resources.claim import Claim, ClaimInsurance, ClaimItem
from fhir.resources.codeableconcept import CodeableConcept
from fhir.resources.codeablereference import CodeableReference
from fhir.resources.coding import Coding
from fhir.resources.communication import Communication, CommunicationPayload
from fhir.resources.communicationrequest import (
    CommunicationRequest,
    CommunicationRequestPayload,
)
from fhir.resources.composition import Composition, CompositionSection
from fhir.resources.condition import Condition
from fhir.resources.contactpoint import ContactPoint
from fhir.resources.coverage import Coverage
from fhir.resources.diagnosticreport import DiagnosticReport
from fhir.resources.documentreference import (
    DocumentReference,
    DocumentReferenceContent,
)
from fhir.resources.encounter import Encounter
from fhir.resources.episodeofcare import EpisodeOfCare
from fhir.resources.extendedcontactdetail import ExtendedContactDetail
from fhir.resources.familymemberhistory import FamilyMemberHistory
from fhir.resources.goal import Goal, GoalTarget
from fhir.resources.humanname import HumanName
from fhir.resources.imagingstudy import ImagingStudy, ImagingStudySeries
from fhir.resources.immunization import Immunization
from fhir.resources.location import Location
from fhir.resources.medication import Medication
from fhir.resources.medicationadministration import (
    MedicationAdministration,
    MedicationAdministrationDosage,
)
from fhir.resources.medicationdispense import MedicationDispense
from fhir.resources.medicationrequest import MedicationRequest
from fhir.resources.medicationstatement import MedicationStatement
from fhir.resources.money import Money
from fhir.resources.narrative import Narrative
from fhir.resources.nutritionorder import (
    NutritionOrder,
    NutritionOrderOralDiet,
)
from fhir.resources.observation import Observation
from fhir.resources.organization import Organization
from fhir.resources.patient import Patient
from fhir.resources.period import Period
from fhir.resources.practitioner import Practitioner, PractitionerQualification
from fhir.resources.practitionerrole import PractitionerRole
from fhir.resources.procedure import Procedure
from fhir.resources.quantity import Quantity
from fhir.resources.reference import Reference
from fhir.resources.relatedperson import RelatedPerson
from fhir.resources.schedule import Schedule
from fhir.resources.servicerequest import ServiceRequest
from fhir.resources.slot import Slot
from fhir.resources.specimen import Specimen, SpecimenCollection
from fhir.resources.task import Task

# --- Plan --------------------------------------------------------------------
#
# Edit this dict to change the relative proportions of generated resources.
# Numbers were chosen to approximate a realistic outpatient EHR mix at
# ~10k Patients, with a long tail of less-common types so every resource type
# in the clinical-workflow set is actually exercised.

PLAN: dict[str, int] = {
    # Administrative
    "Organization": 100,
    "Patient": 10000,
    "Practitioner": 1000,
    "PractitionerRole": 1000,
    "Location": 50,
    "RelatedPerson": 200,
    # Encounter family
    "Encounter": 5000,
    "EpisodeOfCare": 200,
    "Schedule": 100,
    "Slot": 500,
    "Appointment": 500,
    # Clinical
    "Condition": 5000,
    "AllergyIntolerance": 5000,
    "FamilyMemberHistory": 200,
    "Procedure": 2000,
    "Immunization": 2000,
    # Diagnostics
    "Observation": 20000,
    "Specimen": 500,
    "DiagnosticReport": 2000,
    "ImagingStudy": 200,
    # Medication family
    "Medication": 100,
    "MedicationRequest": 5000,
    "MedicationStatement": 500,
    "MedicationDispense": 500,
    "MedicationAdministration": 500,
    # Care planning
    "CarePlan": 1000,
    "Goal": 500,
    "ServiceRequest": 500,
    "Task": 500,
    "NutritionOrder": 200,
    # Documents
    "DocumentReference": 200,
    "Composition": 200,
    # Financial
    "Coverage": 1000,
    "Claim": 500,
    # Communication
    "Communication": 500,
    "CommunicationRequest": 200,
}


# --- BulkContext -------------------------------------------------------------


@dataclass
class BulkContext:
    """Per-run state: the deterministic RNG/Faker, plus the id ledger."""

    scale: float
    seed: int
    faker: Faker = field(init=False)
    rng: random.Random = field(init=False)
    # Holds {resource_type: [resource_id, ...]} so cross-references can sample.
    ids: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))

    def __post_init__(self) -> None:
        self.faker = Faker()
        self.faker.seed_instance(self.seed)
        self.rng = random.Random(self.seed)

    def count(self, resource_type: str) -> int:
        """How many of ``resource_type`` should be produced at the configured scale."""
        target = PLAN.get(resource_type, 0) * self.scale
        # Always emit at least one when asked, otherwise tiny scales silently
        # drop entire resource types.
        return max(1, math.ceil(target)) if target > 0 else 0

    def ledger(self, resource_type: str, resource_id: str) -> None:
        """Record that we generated ``resource_id`` of ``resource_type``."""
        self.ids[resource_type].append(resource_id)

    def random_id(self, resource_type: str) -> str | None:
        ids = self.ids.get(resource_type)
        if not ids:
            return None
        return self.rng.choice(ids)

    def random_ref(self, resource_type: str) -> Reference | None:
        rid = self.random_id(resource_type)
        if rid is None:
            return None
        return Reference(reference=f"{resource_type}/{rid}")


# --- Helpers -----------------------------------------------------------------


def _id_for(resource_type: str, index: int) -> str:
    """Stable, zero-padded id like ``bulk-patient-000042``."""
    # FHIR ids are case-insensitive in practice; lowercase the type so we get
    # predictable, grep-friendly ids regardless of the model class name.
    slug = resource_type.lower()
    return f"bulk-{slug}-{index:06d}"


def _cc(system: str, code: str, display: str | None = None) -> CodeableConcept:
    coding = (
        Coding(system=system, code=code, display=display)
        if display
        else Coding(system=system, code=code)
    )
    return CodeableConcept(coding=[coding])


# --- Vocab pools -------------------------------------------------------------
#
# Small, hand-picked lists keep the generated data plausible without dragging
# in real terminology files. Each helper picks one entry using the ctx RNG so
# selection stays deterministic for a given seed.

_GENDERS = ("male", "female", "other", "unknown")

_OBSERVATION_VITALS: tuple[tuple[str, str, str, float, float], ...] = (
    # (loinc, display, unit, value_min, value_max)
    ("8867-4", "Heart rate", "/min", 55, 105),
    ("8310-5", "Body temperature", "Cel", 36.1, 38.4),
    ("9279-1", "Respiratory rate", "/min", 12, 22),
    ("2708-6", "Oxygen saturation", "%", 92, 100),
    ("29463-7", "Body weight", "kg", 45, 120),
    ("8302-2", "Body height", "cm", 150, 200),
)

_CONDITION_CODES: tuple[tuple[str, str], ...] = (
    ("38341003", "Hypertensive disorder"),
    ("44054006", "Type 2 diabetes mellitus"),
    ("195967001", "Asthma"),
    ("13644009", "Hypercholesterolemia"),
    ("82423001", "Chronic pain"),
    ("64859006", "Osteoporosis"),
    ("840539006", "COVID-19"),
)

_ALLERGY_CODES: tuple[tuple[str, str], ...] = (
    ("373270004", "Penicillin"),
    ("226842001", "Egg"),
    ("227037002", "Peanut"),
    ("412071004", "Latex"),
    ("226934008", "Shellfish"),
)

_PROCEDURE_CODES: tuple[tuple[str, str], ...] = (
    ("80146002", "Appendectomy"),
    ("174041007", "Laparoscopic cholecystectomy"),
    ("16310003", "Total hip replacement"),
    ("392021009", "Lumbar epidural steroid injection"),
    ("265764009", "Renal dialysis"),
)

_IMMUNIZATION_CODES: tuple[tuple[str, str], ...] = (
    ("140", "Influenza, seasonal"),
    ("207", "COVID-19 mRNA LNP-S"),
    ("115", "Tdap"),
    ("33", "Pneumococcal polysaccharide"),
    ("03", "MMR"),
)

_MEDICATION_CODES: tuple[tuple[str, str], ...] = (
    ("308182", "Amoxicillin 500 MG Oral Capsule"),
    ("310798", "Hydrochlorothiazide 25 MG Oral Tablet"),
    ("197361", "Lisinopril 10 MG Oral Tablet"),
    ("314076", "Atorvastatin 20 MG Oral Tablet"),
    ("866924", "Metformin 500 MG Oral Tablet"),
    ("198440", "Albuterol 0.09 MG/ACTUAT inhaler"),
)


# --- Generators --------------------------------------------------------------
#
# Each generator yields tuples of ``(resource_type, resource_id, resource_json)``
# so the caller can pass them straight to FHIRStore.bulk_create.


def _emit(
    ctx: BulkContext, resource: Any, resource_type: str
) -> tuple[str, str, dict[str, Any]]:
    rid = resource.id
    ctx.ledger(resource_type, rid)
    return (resource_type, rid, resource.model_dump(exclude_none=True, mode="json"))


def gen_organizations(ctx: BulkContext) -> Iterator[tuple[str, str, dict]]:
    for i in range(ctx.count("Organization")):
        rid = _id_for("Organization", i)
        is_insurer = i % 5 == 4
        org = Organization(
            id=rid,
            active=True,
            type=[
                _cc(
                    "http://terminology.hl7.org/CodeSystem/organization-type",
                    "ins" if is_insurer else "prov",
                    "Insurance Company" if is_insurer else "Healthcare Provider",
                )
            ],
            name=ctx.faker.company(),
            contact=[
                ExtendedContactDetail(
                    telecom=[ContactPoint(system="phone", value=ctx.faker.phone_number())],
                    address=Address(
                        line=[ctx.faker.street_address()],
                        city=ctx.faker.city(),
                        state=ctx.faker.state_abbr(),
                        postalCode=ctx.faker.zipcode(),
                        country="US",
                    ),
                )
            ],
        )
        yield _emit(ctx, org, "Organization")


def gen_patients(ctx: BulkContext) -> Iterator[tuple[str, str, dict]]:
    for i in range(ctx.count("Patient")):
        rid = _id_for("Patient", i)
        gender = ctx.rng.choice(_GENDERS)
        # Map FHIR gender to Faker's first_name_male/_female so name and gender
        # don't fight each other for the realistic-data majority of patients.
        if gender == "male":
            first = ctx.faker.first_name_male()
        elif gender == "female":
            first = ctx.faker.first_name_female()
        else:
            first = ctx.faker.first_name_nonbinary()
        patient = Patient(
            id=rid,
            active=True,
            name=[HumanName(use="official", family=ctx.faker.last_name(), given=[first])],
            telecom=[
                ContactPoint(system="phone", value=ctx.faker.phone_number(), use="home"),
                ContactPoint(system="email", value=ctx.faker.email()),
            ],
            gender=gender,
            birthDate=ctx.faker.date_of_birth(minimum_age=0, maximum_age=99).isoformat(),
            address=[
                Address(
                    use="home",
                    line=[ctx.faker.street_address()],
                    city=ctx.faker.city(),
                    state=ctx.faker.state_abbr(),
                    postalCode=ctx.faker.zipcode(),
                    country="US",
                )
            ],
        )
        yield _emit(ctx, patient, "Patient")


def gen_practitioners(ctx: BulkContext) -> Iterator[tuple[str, str, dict]]:
    for i in range(ctx.count("Practitioner")):
        rid = _id_for("Practitioner", i)
        is_md = ctx.rng.random() < 0.6
        pract = Practitioner(
            id=rid,
            active=True,
            name=[
                HumanName(
                    use="official",
                    family=ctx.faker.last_name(),
                    given=[ctx.faker.first_name()],
                    prefix=["Dr."] if is_md else ["RN"],
                )
            ],
            telecom=[ContactPoint(system="email", value=ctx.faker.email())],
            qualification=[
                PractitionerQualification(
                    code=_cc(
                        "http://terminology.hl7.org/CodeSystem/v2-0360",
                        "MD" if is_md else "RN",
                        "Doctor of Medicine" if is_md else "Registered Nurse",
                    )
                )
            ],
        )
        yield _emit(ctx, pract, "Practitioner")


def gen_practitioner_roles(ctx: BulkContext) -> Iterator[tuple[str, str, dict]]:
    for i in range(ctx.count("PractitionerRole")):
        rid = _id_for("PractitionerRole", i)
        role = PractitionerRole(
            id=rid,
            active=True,
            practitioner=ctx.random_ref("Practitioner"),
            organization=ctx.random_ref("Organization"),
            code=[
                _cc(
                    "http://terminology.hl7.org/CodeSystem/practitioner-role",
                    "doctor",
                    "Doctor",
                )
            ],
        )
        yield _emit(ctx, role, "PractitionerRole")


def gen_locations(ctx: BulkContext) -> Iterator[tuple[str, str, dict]]:
    for i in range(ctx.count("Location")):
        rid = _id_for("Location", i)
        loc = Location(
            id=rid,
            status="active",
            name=f"{ctx.faker.company()} - Clinic {i + 1}",
            mode="instance",
            managingOrganization=ctx.random_ref("Organization"),
            address=Address(
                line=[ctx.faker.street_address()],
                city=ctx.faker.city(),
                state=ctx.faker.state_abbr(),
                postalCode=ctx.faker.zipcode(),
                country="US",
            ),
        )
        yield _emit(ctx, loc, "Location")


def gen_related_persons(ctx: BulkContext) -> Iterator[tuple[str, str, dict]]:
    relationships = [("SPS", "spouse"), ("CHILD", "child"), ("PRN", "parent"), ("SIB", "sibling")]
    for i in range(ctx.count("RelatedPerson")):
        rid = _id_for("RelatedPerson", i)
        code, display = ctx.rng.choice(relationships)
        rp = RelatedPerson(
            id=rid,
            active=True,
            patient=ctx.random_ref("Patient"),
            relationship=[
                _cc("http://terminology.hl7.org/CodeSystem/v3-RoleCode", code, display)
            ],
            name=[HumanName(family=ctx.faker.last_name(), given=[ctx.faker.first_name()])],
            telecom=[ContactPoint(system="phone", value=ctx.faker.phone_number())],
        )
        yield _emit(ctx, rp, "RelatedPerson")


def gen_encounters(ctx: BulkContext) -> Iterator[tuple[str, str, dict]]:
    classes = (
        ("AMB", "ambulatory"),
        ("EMER", "emergency"),
        ("IMP", "inpatient encounter"),
        ("HH", "home health"),
    )
    for i in range(ctx.count("Encounter")):
        rid = _id_for("Encounter", i)
        cls_code, cls_display = ctx.rng.choice(classes)
        start = ctx.faker.date_time_this_year()
        enc = Encounter(
            id=rid,
            status=ctx.rng.choice(["completed", "in-progress", "planned"]),
            class_fhir=[
                _cc(
                    "http://terminology.hl7.org/CodeSystem/v3-ActCode",
                    cls_code,
                    cls_display,
                )
            ],
            subject=ctx.random_ref("Patient"),
            serviceProvider=ctx.random_ref("Organization"),
            actualPeriod=Period(
                start=start.isoformat() + "Z",
                end=(start.replace(hour=(start.hour + 1) % 24)).isoformat() + "Z",
            ),
        )
        yield _emit(ctx, enc, "Encounter")


def gen_episodes_of_care(ctx: BulkContext) -> Iterator[tuple[str, str, dict]]:
    for i in range(ctx.count("EpisodeOfCare")):
        rid = _id_for("EpisodeOfCare", i)
        ep = EpisodeOfCare(
            id=rid,
            status="active",
            patient=ctx.random_ref("Patient"),
            managingOrganization=ctx.random_ref("Organization"),
            period=Period(start=ctx.faker.date_this_decade().isoformat()),
        )
        yield _emit(ctx, ep, "EpisodeOfCare")


def gen_schedules(ctx: BulkContext) -> Iterator[tuple[str, str, dict]]:
    for i in range(ctx.count("Schedule")):
        rid = _id_for("Schedule", i)
        sch = Schedule(
            id=rid,
            active=True,
            actor=[ctx.random_ref("PractitionerRole")],
            planningHorizon=Period(
                start="2026-01-01T00:00:00Z",
                end="2026-12-31T23:59:59Z",
            ),
        )
        yield _emit(ctx, sch, "Schedule")


def gen_slots(ctx: BulkContext) -> Iterator[tuple[str, str, dict]]:
    for i in range(ctx.count("Slot")):
        rid = _id_for("Slot", i)
        start = ctx.faker.date_time_between(start_date="+1d", end_date="+90d")
        slot = Slot(
            id=rid,
            schedule=ctx.random_ref("Schedule"),
            status=ctx.rng.choice(["free", "busy", "busy-tentative"]),
            start=start.isoformat() + "Z",
            end=start.replace(minute=(start.minute + 30) % 60).isoformat() + "Z",
        )
        yield _emit(ctx, slot, "Slot")


def gen_appointments(ctx: BulkContext) -> Iterator[tuple[str, str, dict]]:
    for i in range(ctx.count("Appointment")):
        rid = _id_for("Appointment", i)
        start = ctx.faker.date_time_between(start_date="+1d", end_date="+60d")
        app = Appointment(
            id=rid,
            status=ctx.rng.choice(["booked", "fulfilled", "cancelled", "noshow"]),
            description="Routine follow-up",
            start=start.isoformat() + "Z",
            end=start.replace(minute=(start.minute + 30) % 60).isoformat() + "Z",
            participant=[
                AppointmentParticipant(actor=ctx.random_ref("Patient"), status="accepted"),
                AppointmentParticipant(actor=ctx.random_ref("Practitioner"), status="accepted"),
            ],
        )
        yield _emit(ctx, app, "Appointment")


def gen_conditions(ctx: BulkContext) -> Iterator[tuple[str, str, dict]]:
    for i in range(ctx.count("Condition")):
        rid = _id_for("Condition", i)
        snomed, display = ctx.rng.choice(_CONDITION_CODES)
        cond = Condition(
            id=rid,
            clinicalStatus=_cc(
                "http://terminology.hl7.org/CodeSystem/condition-clinical",
                ctx.rng.choice(["active", "resolved", "recurrence"]),
            ),
            verificationStatus=_cc(
                "http://terminology.hl7.org/CodeSystem/condition-ver-status",
                "confirmed",
                "Confirmed",
            ),
            code=_cc("http://snomed.info/sct", snomed, display),
            subject=ctx.random_ref("Patient"),
            encounter=ctx.random_ref("Encounter"),
            recordedDate=ctx.faker.date_time_this_decade().isoformat() + "Z",
        )
        yield _emit(ctx, cond, "Condition")


def gen_allergies(ctx: BulkContext) -> Iterator[tuple[str, str, dict]]:
    for i in range(ctx.count("AllergyIntolerance")):
        rid = _id_for("AllergyIntolerance", i)
        snomed, display = ctx.rng.choice(_ALLERGY_CODES)
        allergy = AllergyIntolerance(
            id=rid,
            clinicalStatus=_cc(
                "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical",
                "active",
                "Active",
            ),
            verificationStatus=_cc(
                "http://terminology.hl7.org/CodeSystem/allergyintolerance-verification",
                "confirmed",
                "Confirmed",
            ),
            type=_cc(
                "http://hl7.org/fhir/allergy-intolerance-type", "allergy", "Allergy"
            ),
            criticality=ctx.rng.choice(["low", "high", "unable-to-assess"]),
            code=_cc("http://snomed.info/sct", snomed, display),
            patient=ctx.random_ref("Patient"),
            recordedDate=ctx.faker.date_time_this_decade().isoformat() + "Z",
        )
        yield _emit(ctx, allergy, "AllergyIntolerance")


def gen_family_histories(ctx: BulkContext) -> Iterator[tuple[str, str, dict]]:
    relationships = [("FTH", "father"), ("MTH", "mother"), ("SIB", "sibling")]
    for i in range(ctx.count("FamilyMemberHistory")):
        rid = _id_for("FamilyMemberHistory", i)
        code, display = ctx.rng.choice(relationships)
        fmh = FamilyMemberHistory(
            id=rid,
            status="completed",
            patient=ctx.random_ref("Patient"),
            date=ctx.faker.date_this_decade().isoformat(),
            name=f"Patient's {display}",
            relationship=_cc(
                "http://terminology.hl7.org/CodeSystem/v3-RoleCode", code, display
            ),
        )
        yield _emit(ctx, fmh, "FamilyMemberHistory")


def gen_procedures(ctx: BulkContext) -> Iterator[tuple[str, str, dict]]:
    for i in range(ctx.count("Procedure")):
        rid = _id_for("Procedure", i)
        snomed, display = ctx.rng.choice(_PROCEDURE_CODES)
        proc = Procedure(
            id=rid,
            status=ctx.rng.choice(["completed", "in-progress", "preparation"]),
            code=_cc("http://snomed.info/sct", snomed, display),
            subject=ctx.random_ref("Patient"),
            encounter=ctx.random_ref("Encounter"),
            occurrenceDateTime=ctx.faker.date_time_this_decade().isoformat() + "Z",
            note=[Annotation(text="Procedure completed without complications.")],
        )
        yield _emit(ctx, proc, "Procedure")


def gen_immunizations(ctx: BulkContext) -> Iterator[tuple[str, str, dict]]:
    for i in range(ctx.count("Immunization")):
        rid = _id_for("Immunization", i)
        cvx, display = ctx.rng.choice(_IMMUNIZATION_CODES)
        imm = Immunization(
            id=rid,
            status="completed",
            vaccineCode=_cc("http://hl7.org/fhir/sid/cvx", cvx, display),
            patient=ctx.random_ref("Patient"),
            occurrenceDateTime=ctx.faker.date_time_this_decade().isoformat() + "Z",
            primarySource=True,
            lotNumber=ctx.faker.bothify(text="??###-###"),
        )
        yield _emit(ctx, imm, "Immunization")


def gen_observations(ctx: BulkContext) -> Iterator[tuple[str, str, dict]]:
    vital_signs_cat = _cc(
        "http://terminology.hl7.org/CodeSystem/observation-category",
        "vital-signs",
        "Vital Signs",
    )
    for i in range(ctx.count("Observation")):
        rid = _id_for("Observation", i)
        loinc, display, unit, lo, hi = ctx.rng.choice(_OBSERVATION_VITALS)
        # Round most vitals to whole numbers; temperature stays decimal.
        if loinc == "8310-5":
            value: float | int = round(ctx.rng.uniform(lo, hi), 1)
        else:
            value = ctx.rng.randint(int(lo), int(hi))
        obs = Observation(
            id=rid,
            status="final",
            category=[vital_signs_cat],
            code=_cc("http://loinc.org", loinc, display),
            subject=ctx.random_ref("Patient"),
            encounter=ctx.random_ref("Encounter"),
            effectiveDateTime=ctx.faker.date_time_this_year().isoformat() + "Z",
            valueQuantity=Quantity(
                value=value,
                unit=unit,
                system="http://unitsofmeasure.org",
                code=unit,
            ),
        )
        yield _emit(ctx, obs, "Observation")


def gen_specimens(ctx: BulkContext) -> Iterator[tuple[str, str, dict]]:
    types = [
        ("119297000", "Blood specimen"),
        ("258574006", "Urine specimen"),
        ("119334006", "Sputum specimen"),
    ]
    for i in range(ctx.count("Specimen")):
        rid = _id_for("Specimen", i)
        snomed, display = ctx.rng.choice(types)
        s = Specimen(
            id=rid,
            status="available",
            type=_cc("http://snomed.info/sct", snomed, display),
            subject=ctx.random_ref("Patient"),
            collection=SpecimenCollection(
                collectedDateTime=ctx.faker.date_time_this_year().isoformat() + "Z",
                quantity=Quantity(value=ctx.rng.randint(2, 20), unit="mL"),
            ),
        )
        yield _emit(ctx, s, "Specimen")


def gen_diagnostic_reports(ctx: BulkContext) -> Iterator[tuple[str, str, dict]]:
    lab_cat = _cc(
        "http://terminology.hl7.org/CodeSystem/observation-category",
        "laboratory",
        "Laboratory",
    )
    panels = [
        ("58410-2", "Complete blood count (hemogram) panel"),
        ("24323-8", "Comprehensive metabolic 2000 panel"),
        ("57698-3", "Lipid panel with direct LDL"),
    ]
    for i in range(ctx.count("DiagnosticReport")):
        rid = _id_for("DiagnosticReport", i)
        loinc, display = ctx.rng.choice(panels)
        # Only attach a specimen ~30% of the time so we exercise both shapes.
        specimen = ctx.random_ref("Specimen") if ctx.rng.random() < 0.3 else None
        dr = DiagnosticReport(
            id=rid,
            status="final",
            category=[lab_cat],
            code=_cc("http://loinc.org", loinc, display),
            subject=ctx.random_ref("Patient"),
            encounter=ctx.random_ref("Encounter"),
            effectiveDateTime=ctx.faker.date_time_this_year().isoformat() + "Z",
            issued=ctx.faker.date_time_this_year().isoformat() + "Z",
            specimen=[specimen] if specimen else None,
            conclusion=ctx.rng.choice(
                ["Within normal limits.", "Mild abnormality, follow-up suggested.", "Critical value flagged."]
            ),
        )
        yield _emit(ctx, dr, "DiagnosticReport")


def gen_imaging_studies(ctx: BulkContext) -> Iterator[tuple[str, str, dict]]:
    modalities = (
        ("CR", "Computed Radiography"),
        ("CT", "Computed Tomography"),
        ("MR", "Magnetic Resonance"),
        ("US", "Ultrasound"),
    )
    for i in range(ctx.count("ImagingStudy")):
        rid = _id_for("ImagingStudy", i)
        mod_code, mod_display = ctx.rng.choice(modalities)
        st = ImagingStudy(
            id=rid,
            status="available",
            subject=ctx.random_ref("Patient"),
            encounter=ctx.random_ref("Encounter"),
            started=ctx.faker.date_time_this_year().isoformat() + "Z",
            numberOfSeries=1,
            numberOfInstances=2,
            description=f"{mod_display} study",
            series=[
                ImagingStudySeries(
                    uid=ctx.faker.uuid4(),
                    number=1,
                    modality=_cc(
                        "http://dicom.nema.org/resources/ontology/DCM",
                        mod_code,
                        mod_display,
                    ),
                    numberOfInstances=2,
                    description=f"{mod_display} series 1",
                )
            ],
        )
        yield _emit(ctx, st, "ImagingStudy")


def gen_medications(ctx: BulkContext) -> Iterator[tuple[str, str, dict]]:
    for i in range(ctx.count("Medication")):
        rid = _id_for("Medication", i)
        rxnorm, display = ctx.rng.choice(_MEDICATION_CODES)
        med = Medication(
            id=rid,
            status="active",
            code=_cc("http://www.nlm.nih.gov/research/umls/rxnorm", rxnorm, display),
        )
        yield _emit(ctx, med, "Medication")


def _medication_ref(ctx: BulkContext) -> CodeableReference:
    """CodeableReference that points at a generated Medication, or falls back
    to an inline CodeableConcept if no Medications were generated."""
    ref = ctx.random_ref("Medication")
    rxnorm, display = ctx.rng.choice(_MEDICATION_CODES)
    concept = _cc("http://www.nlm.nih.gov/research/umls/rxnorm", rxnorm, display)
    return CodeableReference(concept=concept, reference=ref) if ref else CodeableReference(concept=concept)


def gen_medication_requests(ctx: BulkContext) -> Iterator[tuple[str, str, dict]]:
    for i in range(ctx.count("MedicationRequest")):
        rid = _id_for("MedicationRequest", i)
        mr = MedicationRequest(
            id=rid,
            status=ctx.rng.choice(["active", "completed", "stopped"]),
            intent="order",
            priority="routine",
            medication=_medication_ref(ctx),
            subject=ctx.random_ref("Patient"),
            authoredOn=ctx.faker.date_time_this_year().isoformat() + "Z",
            requester=ctx.random_ref("Practitioner"),
        )
        yield _emit(ctx, mr, "MedicationRequest")


def gen_medication_statements(ctx: BulkContext) -> Iterator[tuple[str, str, dict]]:
    for i in range(ctx.count("MedicationStatement")):
        rid = _id_for("MedicationStatement", i)
        ms = MedicationStatement(
            id=rid,
            status="recorded",
            medication=_medication_ref(ctx),
            subject=ctx.random_ref("Patient"),
            effectiveDateTime=ctx.faker.date_time_this_decade().isoformat() + "Z",
            dateAsserted=ctx.faker.date_this_year().isoformat(),
        )
        yield _emit(ctx, ms, "MedicationStatement")


def gen_medication_dispenses(ctx: BulkContext) -> Iterator[tuple[str, str, dict]]:
    for i in range(ctx.count("MedicationDispense")):
        rid = _id_for("MedicationDispense", i)
        md = MedicationDispense(
            id=rid,
            status="completed",
            medication=_medication_ref(ctx),
            subject=ctx.random_ref("Patient"),
            quantity=Quantity(value=ctx.rng.randint(7, 90), unit="tablet"),
            whenHandedOver=ctx.faker.date_time_this_year().isoformat() + "Z",
        )
        yield _emit(ctx, md, "MedicationDispense")


def gen_medication_administrations(ctx: BulkContext) -> Iterator[tuple[str, str, dict]]:
    for i in range(ctx.count("MedicationAdministration")):
        rid = _id_for("MedicationAdministration", i)
        ma = MedicationAdministration(
            id=rid,
            status="completed",
            medication=_medication_ref(ctx),
            subject=ctx.random_ref("Patient"),
            encounter=ctx.random_ref("Encounter"),
            occurenceDateTime=ctx.faker.date_time_this_year().isoformat() + "Z",
            dosage=MedicationAdministrationDosage(
                text="As directed",
                dose=Quantity(
                    value=ctx.rng.randint(1, 1000),
                    unit="mg",
                    system="http://unitsofmeasure.org",
                    code="mg",
                ),
            ),
        )
        yield _emit(ctx, ma, "MedicationAdministration")


def gen_care_plans(ctx: BulkContext) -> Iterator[tuple[str, str, dict]]:
    for i in range(ctx.count("CarePlan")):
        rid = _id_for("CarePlan", i)
        cp = CarePlan(
            id=rid,
            status="active",
            intent="plan",
            title="Chronic disease management",
            subject=ctx.random_ref("Patient"),
            encounter=ctx.random_ref("Encounter"),
            period=Period(start=ctx.faker.date_this_year().isoformat()),
        )
        yield _emit(ctx, cp, "CarePlan")


def gen_goals(ctx: BulkContext) -> Iterator[tuple[str, str, dict]]:
    for i in range(ctx.count("Goal")):
        rid = _id_for("Goal", i)
        g = Goal(
            id=rid,
            lifecycleStatus=ctx.rng.choice(["active", "completed", "on-hold"]),
            description=_cc("http://snomed.info/sct", "418995006", "Patient health goal"),
            subject=ctx.random_ref("Patient"),
            startDate=ctx.faker.date_this_year().isoformat(),
            target=[
                GoalTarget(
                    measure=_cc("http://loinc.org", "8480-6", "Systolic blood pressure"),
                    detailQuantity=Quantity(
                        value=130,
                        comparator="<",
                        unit="mmHg",
                        system="http://unitsofmeasure.org",
                        code="mm[Hg]",
                    ),
                )
            ],
        )
        yield _emit(ctx, g, "Goal")


def gen_service_requests(ctx: BulkContext) -> Iterator[tuple[str, str, dict]]:
    services = (
        ("306203009", "Referral to cardiologist"),
        ("306206001", "Referral to dietitian"),
        ("306181004", "Referral to physiotherapist"),
    )
    for i in range(ctx.count("ServiceRequest")):
        rid = _id_for("ServiceRequest", i)
        snomed, display = ctx.rng.choice(services)
        sr = ServiceRequest(
            id=rid,
            status="active",
            intent="order",
            priority="routine",
            code=CodeableReference(concept=_cc("http://snomed.info/sct", snomed, display)),
            subject=ctx.random_ref("Patient"),
            encounter=ctx.random_ref("Encounter"),
            authoredOn=ctx.faker.date_time_this_year().isoformat() + "Z",
            requester=ctx.random_ref("Practitioner"),
        )
        yield _emit(ctx, sr, "ServiceRequest")


def gen_tasks(ctx: BulkContext) -> Iterator[tuple[str, str, dict]]:
    for i in range(ctx.count("Task")):
        rid = _id_for("Task", i)
        t = Task(
            id=rid,
            status=ctx.rng.choice(["ready", "in-progress", "completed"]),
            intent="order",
            priority="routine",
            description="Follow-up action item",
            authoredOn=ctx.faker.date_time_this_year().isoformat() + "Z",
            owner=ctx.random_ref("PractitionerRole"),
        )
        yield _emit(ctx, t, "Task")


def gen_nutrition_orders(ctx: BulkContext) -> Iterator[tuple[str, str, dict]]:
    diets = (
        ("386619000", "Low sodium diet"),
        ("182954008", "Diabetic diet"),
        ("226234005", "Healthy diet"),
    )
    for i in range(ctx.count("NutritionOrder")):
        rid = _id_for("NutritionOrder", i)
        snomed, display = ctx.rng.choice(diets)
        no = NutritionOrder(
            id=rid,
            status="active",
            intent="order",
            subject=ctx.random_ref("Patient"),
            encounter=ctx.random_ref("Encounter"),
            dateTime=ctx.faker.date_time_this_year().isoformat() + "Z",
            oralDiet=NutritionOrderOralDiet(
                type=[_cc("http://snomed.info/sct", snomed, display)]
            ),
        )
        yield _emit(ctx, no, "NutritionOrder")


def gen_document_references(ctx: BulkContext) -> Iterator[tuple[str, str, dict]]:
    for i in range(ctx.count("DocumentReference")):
        rid = _id_for("DocumentReference", i)
        note_text = ctx.faker.paragraph(nb_sentences=3)
        data_b64 = base64.b64encode(note_text.encode("utf-8")).decode("ascii")
        dr = DocumentReference(
            id=rid,
            status="current",
            docStatus="final",
            type=_cc("http://loinc.org", "11506-3", "Progress note"),
            subject=ctx.random_ref("Patient"),
            date=ctx.faker.date_time_this_year().isoformat() + "Z",
            content=[
                DocumentReferenceContent(
                    attachment=Attachment(
                        contentType="text/plain",
                        language="en-US",
                        data=data_b64,
                        title="Clinical note",
                    )
                )
            ],
        )
        yield _emit(ctx, dr, "DocumentReference")


def gen_compositions(ctx: BulkContext) -> Iterator[tuple[str, str, dict]]:
    for i in range(ctx.count("Composition")):
        rid = _id_for("Composition", i)
        c = Composition(
            id=rid,
            status="final",
            type=_cc("http://loinc.org", "18842-5", "Discharge summary"),
            subject=[ctx.random_ref("Patient")],
            encounter=ctx.random_ref("Encounter"),
            date=ctx.faker.date_time_this_year().isoformat() + "Z",
            author=[ctx.random_ref("Practitioner")],
            name=f"discharge-summary-{i:06d}",
            title="Discharge Summary",
            section=[
                CompositionSection(
                    title="Summary",
                    text=Narrative(
                        status="generated",
                        div=(
                            "<div xmlns='http://www.w3.org/1999/xhtml'>"
                            f"<p>{ctx.faker.sentence()}</p></div>"
                        ),
                    ),
                )
            ],
        )
        yield _emit(ctx, c, "Composition")


def gen_coverages(ctx: BulkContext) -> Iterator[tuple[str, str, dict]]:
    for i in range(ctx.count("Coverage")):
        rid = _id_for("Coverage", i)
        c = Coverage(
            id=rid,
            status="active",
            kind="insurance",
            beneficiary=ctx.random_ref("Patient"),
            policyHolder=ctx.random_ref("Patient"),
            period=Period(start="2026-01-01", end="2026-12-31"),
            insurer=ctx.random_ref("Organization"),
            relationship=_cc(
                "http://terminology.hl7.org/CodeSystem/subscriber-relationship",
                "self",
                "Self",
            ),
        )
        yield _emit(ctx, c, "Coverage")


def gen_claims(ctx: BulkContext) -> Iterator[tuple[str, str, dict]]:
    for i in range(ctx.count("Claim")):
        rid = _id_for("Claim", i)
        amount = round(ctx.rng.uniform(50, 5000), 2)
        c = Claim(
            id=rid,
            status="active",
            type=_cc(
                "http://terminology.hl7.org/CodeSystem/claim-type",
                "professional",
                "Professional",
            ),
            use="claim",
            patient=ctx.random_ref("Patient"),
            created=ctx.faker.date_time_this_year().isoformat() + "Z",
            provider=ctx.random_ref("Organization"),
            priority=_cc(
                "http://terminology.hl7.org/CodeSystem/processpriority",
                "normal",
                "Normal",
            ),
            insurance=[
                ClaimInsurance(
                    sequence=1,
                    focal=True,
                    coverage=ctx.random_ref("Coverage"),
                )
            ],
            item=[
                ClaimItem(
                    sequence=1,
                    productOrService=_cc(
                        "http://www.ama-assn.org/go/cpt", "99213", "Office visit"
                    ),
                    servicedDate=ctx.faker.date_this_year().isoformat(),
                    unitPrice=Money(value=amount, currency="USD"),
                    net=Money(value=amount, currency="USD"),
                )
            ],
        )
        yield _emit(ctx, c, "Claim")


def gen_communications(ctx: BulkContext) -> Iterator[tuple[str, str, dict]]:
    for i in range(ctx.count("Communication")):
        rid = _id_for("Communication", i)
        msg = ctx.faker.sentence(nb_words=10)
        data_b64 = base64.b64encode(msg.encode("utf-8")).decode("ascii")
        c = Communication(
            id=rid,
            status="completed",
            priority="routine",
            subject=ctx.random_ref("Patient"),
            sent=ctx.faker.date_time_this_year().isoformat() + "Z",
            sender=ctx.random_ref("Practitioner"),
            recipient=[ctx.random_ref("Practitioner")],
            payload=[
                CommunicationPayload(
                    contentAttachment=Attachment(
                        contentType="text/plain",
                        language="en-US",
                        title="Message",
                        data=data_b64,
                    )
                )
            ],
        )
        yield _emit(ctx, c, "Communication")


def gen_communication_requests(ctx: BulkContext) -> Iterator[tuple[str, str, dict]]:
    for i in range(ctx.count("CommunicationRequest")):
        rid = _id_for("CommunicationRequest", i)
        msg = ctx.faker.sentence(nb_words=10)
        data_b64 = base64.b64encode(msg.encode("utf-8")).decode("ascii")
        cr = CommunicationRequest(
            id=rid,
            status="active",
            intent="order",
            priority="routine",
            subject=ctx.random_ref("Patient"),
            authoredOn=ctx.faker.date_time_this_year().isoformat() + "Z",
            requester=ctx.random_ref("Practitioner"),
            recipient=[ctx.random_ref("PractitionerRole")],
            payload=[
                CommunicationRequestPayload(
                    contentAttachment=Attachment(
                        contentType="text/plain",
                        language="en-US",
                        title="Request",
                        data=data_b64,
                    )
                )
            ],
        )
        yield _emit(ctx, cr, "CommunicationRequest")


# --- Orchestration -----------------------------------------------------------
#
# The order in PIPELINE is the dependency order: each entry's generator can
# only sample from types produced by earlier entries.

PIPELINE: list[tuple[str, Callable[[BulkContext], Iterator[tuple[str, str, dict]]]]] = [
    ("Organization", gen_organizations),
    ("Patient", gen_patients),
    ("Practitioner", gen_practitioners),
    ("PractitionerRole", gen_practitioner_roles),
    ("Location", gen_locations),
    ("RelatedPerson", gen_related_persons),
    ("Encounter", gen_encounters),
    ("EpisodeOfCare", gen_episodes_of_care),
    ("Schedule", gen_schedules),
    ("Slot", gen_slots),
    ("Appointment", gen_appointments),
    ("Condition", gen_conditions),
    ("AllergyIntolerance", gen_allergies),
    ("FamilyMemberHistory", gen_family_histories),
    ("Procedure", gen_procedures),
    ("Immunization", gen_immunizations),
    ("Observation", gen_observations),
    ("Specimen", gen_specimens),
    ("DiagnosticReport", gen_diagnostic_reports),
    ("ImagingStudy", gen_imaging_studies),
    ("Medication", gen_medications),
    ("MedicationRequest", gen_medication_requests),
    ("MedicationStatement", gen_medication_statements),
    ("MedicationDispense", gen_medication_dispenses),
    ("MedicationAdministration", gen_medication_administrations),
    ("CarePlan", gen_care_plans),
    ("Goal", gen_goals),
    ("ServiceRequest", gen_service_requests),
    ("Task", gen_tasks),
    ("NutritionOrder", gen_nutrition_orders),
    ("DocumentReference", gen_document_references),
    ("Composition", gen_compositions),
    ("Coverage", gen_coverages),
    ("Claim", gen_claims),
    ("Communication", gen_communications),
    ("CommunicationRequest", gen_communication_requests),
]


def generate(
    ctx: BulkContext,
    only_types: set[str] | None = None,
) -> Iterator[tuple[str, str, dict[str, Any]]]:
    """Yield every resource the plan requests, in dependency order.

    When ``only_types`` is provided, types not in the set are still generated
    if a kept type depends on them (e.g. requesting only ``Observation`` still
    produces the Patients and Encounters its references point at, otherwise
    every Observation would have a dangling ``subject``).
    """
    required = _expand_dependencies(only_types) if only_types else None
    for resource_type, gen in PIPELINE:
        if required is not None and resource_type not in required:
            continue
        yield from gen(ctx)


# Static dependency map between resource types in the pipeline. We could
# derive it from the generators but that needs reflection; a manual table is
# clearer and impossible to drift from the actual references because the
# generators above use the exact same canonical type strings.
_DEPS: dict[str, frozenset[str]] = {
    "Organization": frozenset(),
    "Patient": frozenset(),
    "Practitioner": frozenset(),
    "PractitionerRole": frozenset({"Practitioner", "Organization"}),
    "Location": frozenset({"Organization"}),
    "RelatedPerson": frozenset({"Patient"}),
    "Encounter": frozenset({"Patient", "Organization"}),
    "EpisodeOfCare": frozenset({"Patient", "Organization"}),
    "Schedule": frozenset({"PractitionerRole"}),
    "Slot": frozenset({"Schedule"}),
    "Appointment": frozenset({"Patient", "Practitioner"}),
    "Condition": frozenset({"Patient", "Encounter"}),
    "AllergyIntolerance": frozenset({"Patient"}),
    "FamilyMemberHistory": frozenset({"Patient"}),
    "Procedure": frozenset({"Patient", "Encounter"}),
    "Immunization": frozenset({"Patient"}),
    "Observation": frozenset({"Patient", "Encounter"}),
    "Specimen": frozenset({"Patient"}),
    "DiagnosticReport": frozenset({"Patient", "Encounter", "Specimen"}),
    "ImagingStudy": frozenset({"Patient", "Encounter"}),
    "Medication": frozenset(),
    "MedicationRequest": frozenset({"Patient", "Practitioner", "Medication"}),
    "MedicationStatement": frozenset({"Patient", "Medication"}),
    "MedicationDispense": frozenset({"Patient", "Medication"}),
    "MedicationAdministration": frozenset({"Patient", "Encounter", "Medication"}),
    "CarePlan": frozenset({"Patient", "Encounter"}),
    "Goal": frozenset({"Patient"}),
    "ServiceRequest": frozenset({"Patient", "Encounter", "Practitioner"}),
    "Task": frozenset({"PractitionerRole"}),
    "NutritionOrder": frozenset({"Patient", "Encounter"}),
    "DocumentReference": frozenset({"Patient"}),
    "Composition": frozenset({"Patient", "Encounter", "Practitioner"}),
    "Coverage": frozenset({"Patient", "Organization"}),
    "Claim": frozenset({"Patient", "Organization", "Coverage"}),
    "Communication": frozenset({"Patient", "Practitioner"}),
    "CommunicationRequest": frozenset({"Patient", "Practitioner", "PractitionerRole"}),
}


def _expand_dependencies(types: set[str]) -> set[str]:
    """Return ``types`` plus the transitive closure of their dependencies."""
    closure = set(types)
    queue = list(types)
    while queue:
        t = queue.pop()
        for dep in _DEPS.get(t, frozenset()):
            if dep not in closure:
                closure.add(dep)
                queue.append(dep)
    return closure


def known_resource_types() -> list[str]:
    return [rt for rt, _ in PIPELINE]
