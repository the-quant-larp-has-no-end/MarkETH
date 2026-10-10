"""The degree catalogue: the levels and programmes a profile may name (SPEC §3).

A profile states a level and one programme of that level. The value of a programme is
its label: `users.degree_programme` holds the text below, and every screen shows it as
it is. A pair is checked against this catalogue by the profile service, not by the
database, so the list can change without the schema changing.

The programmes are ETH Zurich's, as ethz.ch listed them on 9 October 2026: the degree
programmes of the Bachelor's and Master's level, the sixteen departments a doctorate is
done in (the doctoral programmes and graduate schools are left out: a doctoral student
belongs to one of the departments either way), the continuing-education programmes (MAS,
DAS, CAS, MBA) and the teaching diplomas and certificates. The level OTHER is MarkETH's
own, so that ETH members who are in no programme can register.

This is reference data, not a tunable, so it is not in `.env`. `GET /config` serves it,
and the frontend builds both selects from there.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from app.core.enums import DegreeLevel

PROGRAMME_MAX_LENGTH: Final[int] = 100
"""Width of `users.degree_programme`, and the longest label a request may carry."""


@dataclass(frozen=True, slots=True)
class DegreeLevelEntry:
    level: DegreeLevel
    name: str
    """How the level is shown, for example "Continuing education"."""

    programmes: tuple[str, ...]
    """The labels of the level, in the order they are offered."""


CATALOGUE: Final[tuple[DegreeLevelEntry, ...]] = (
    DegreeLevelEntry(
        level=DegreeLevel.BACHELOR,
        name="Bachelor",
        programmes=(
            "BA Public Policy (Professional Officer)",
            "BSc Agricultural Sciences",
            "BSc Architecture",
            "BSc Biochemistry – Chemical Biology",
            "BSc Biology",
            "BSc Chemical Engineering",
            "BSc Chemistry",
            "BSc Civil Engineering",
            "BSc Computational Science and Engineering",
            "BSc Computer Science",
            "BSc Earth and Climate Sciences",
            "BSc Electrical Engineering and Information Technology",
            "BSc Environmental Engineering",
            "BSc Environmental Sciences",
            "BSc Food Science and Nutrition",
            "BSc Geospatial Engineering",
            "BSc Health Sciences and Technology",
            "BSc Human Medicine",
            "BSc Interdisciplinary Sciences",
            "BSc Materials Science",
            "BSc Mathematics",
            "BSc Mechanical Engineering",
            "BSc Pharmaceutical Sciences",
            "BSc Physics",
        ),
    ),
    DegreeLevelEntry(
        level=DegreeLevel.MASTER,
        name="Master",
        programmes=(
            "MA Comparative and International Studies",
            "MA History and Philosophy of Knowledge",
            "MA Mathematics Didactics",
            "MA Science Didactics",
            "MSc Agricultural Sciences",
            "MSc Applied Geophysics",
            "MSc Architecture",
            "MSc Atmospheric and Climate Science",
            "MSc Biochemistry – Chemical Biology",
            "MSc Biology",
            "MSc Biomedical Engineering",
            "MSc Biotechnology",
            "MSc Chemical and Bioengineering",
            "MSc Chemistry",
            "MSc Civil Engineering",
            "MSc Computational Biology and Bioinformatics",
            "MSc Computational Science and Engineering",
            "MSc Computer Science",
            "MSc Cyber Security",
            "MSc Data Science",
            "MSc Earth Sciences",
            "MSc Electrical Engineering and Information Technology",
            "MSc Energy Science and Technology",
            "MSc Environmental Engineering",
            "MSc Environmental Sciences",
            "MSc Food Science and Nutrition",
            "MSc Geomatics",
            "MSc Health Sciences and Technology",
            "MSc High Energy Physics",
            "MSc Integrated Building Systems",
            "MSc Interdisciplinary Brain Sciences",
            "MSc Interdisciplinary Sciences",
            "MSc Landscape Architecture",
            "MSc Management, Technology, and Economics",
            "MSc Materials Science and Engineering",
            "MSc Mathematics/Applied Mathematics",
            "MSc Mechanical Engineering",
            "MSc Micro and Nanosystems",
            "MSc Neural Systems and Computation",
            "MSc Nuclear Engineering",
            "MSc Pharmaceutical Sciences",
            "MSc Pharmacy",
            "MSc Physics",
            "MSc Process Engineering",
            "MSc Quantitative Finance",
            "MSc Quantum Engineering",
            "MSc Robotics, Systems and Control",
            "MSc Science, Technology and Policy",
            "MSc Space Systems",
            "MSc Spatial Development and Infrastructure Systems",
            "MSc Statistics",
        ),
    ),
    DegreeLevelEntry(
        level=DegreeLevel.DOCTORATE,
        name="Doctorate",
        programmes=(
            "Doctorate Architecture",
            "Doctorate Biology",
            "Doctorate Biosystems Science and Engineering",
            "Doctorate Chemistry and Applied Biosciences",
            "Doctorate Civil, Environmental and Geomatic Engineering",
            "Doctorate Computer Science",
            "Doctorate Earth and Planetary Sciences",
            "Doctorate Environmental Systems Science",
            "Doctorate Health Sciences and Technology",
            "Doctorate Humanities, Social and Political Sciences",
            "Doctorate Information Technology and Electrical Engineering",
            "Doctorate Management, Technology and Economics",
            "Doctorate Materials",
            "Doctorate Mathematics",
            "Doctorate Mechanical and Process Engineering",
            "Doctorate Physics",
        ),
    ),
    DegreeLevelEntry(
        level=DegreeLevel.CONTINUING_EDUCATION,
        name="Continuing education",
        programmes=(
            "CAS Advanced Materials and Processes",
            "CAS AI and Software Development",
            "CAS AI, Data and Machine Learning",
            "CAS Angewandten Erdwissenschaften",
            "CAS Applied Automation Technology",
            "CAS Applied Electronics and Digitization",
            "CAS Applied Machine Learning & Information Processing",
            "CAS Applied Manufacturing Technology",
            "CAS Applied Statistical Data Science",
            "CAS Applied Technology in Energy",
            "CAS Applied Technology: R&D and Innovation",
            "CAS Climate Innovation",
            "CAS Cloud and Mobile Computing",
            "CAS Computer Science",
            "CAS Cyber Security",
            "CAS Digital Clinical Trials",
            "CAS Digital Health: Therapeutics",
            "CAS Entrepreneurial Leadership in Technology Ventures",
            "CAS Future Heritage",
            "CAS Future Mobility",
            "CAS Future of Spatial Development",
            "CAS GenAI and Agentic Systems for Enterprises",
            "CAS Geoinformationssysteme und -analysen",
            "CAS Global Cooperation and Sustainable Development",
            "CAS Infrastructure Construction Management",
            "CAS Klinische Pharmazie",
            "CAS Machine Learning in Finance and Insurance",
            "CAS Naturgefahren-Risikomanagement",
            "CAS Nutrition for Disease Prevention and Health",
            "CAS Nutrition in Medicine",
            "CAS Offizinpharmazie",
            "CAS Pharmaceuticals – From Research to Market",
            "CAS Preservation",
            "CAS Public Governance and Administration",
            "CAS Quantum Science",
            "CAS Radiopharmaceutical Chemistry/Radiopharmacy",
            "CAS Regenerative Materials – Essentials",
            "CAS Regenerative Materials – Hygrothermal Specialisation",
            "CAS Regenerative Materials – Structural Specialisation",
            "CAS Regenerative Systems: Beyond Systems Thinking",
            "CAS Regenerative Systems: Sustainability to Regeneration",
            "CAS Regenerative Systems: Systemic Design",
            "CAS Regulatory Thinking",
            "CAS Repair and Maintenance",
            "CAS Robotics",
            "CAS Seismic Evaluation and Retrofitting",
            "CAS Spatial Development and Planning Practice",
            "CAS Spatial Development and Process Design",
            "CAS Technology and Public Policy: Impact Analysis",
            "CAS Technology and Public Policy: Policy Process",
            "DAS Applied Statistical Data Science",
            "DAS Cyber Security",
            "DAS Data Science",
            "DAS Geschichte und Theorie der Architektur",
            "DAS Information Technology and Electrical Engineering",
            "DAS Military Sciences",
            "DAS Regenerative Materials",
            "DAS Spitalpharmazie",
            "DAS Vorbereitung auf die eidgenössische Prüfung in Pharmazie",
            "Executive MBA ETH HSG",
            "MAS Advanced Fundamentals of Mechatronics Engineering",
            "MAS AI and Digital Technology",
            "MAS Applied Technology",
            "MAS Architecture and Digital Fabrication",
            "MAS Built Heritage and Repair",
            "MAS Collective Housing",
            "MAS Cyber Security",
            "MAS digital Clinical Research",
            "MAS Fire Safety Engineering",
            "MAS Geschichte und Theorie der Architektur",
            "MAS Global Cooperation and Sustainable Development",
            "MAS Housing",
            "MAS International Governance and Law",
            "MAS Management, Technology, and Economics",
            "MAS Mediation in Peace Processes",
            "MAS Medical Physics",
            "MAS Nutrition and Health",
            "MAS Preservation and Construction History",
            "MAS Regenerative Systems",
            "MAS Spatial Development",
            "MAS Technology and Public Policy",
            "MAS Urban and Territorial Design",
        ),
    ),
    DegreeLevelEntry(
        level=DegreeLevel.TEACHER_EDUCATION,
        name="Teacher education",
        programmes=(
            "Teaching Certificate Agricultural Sciences",
            "Teaching Certificate Computer Science",
            "Teaching Certificate Electrical Engineering and Information Technology",
            "Teaching Certificate Environmental Studies",
            "Teaching Certificate Food Science",
            "Teaching Certificate Health Sciences and Technology",
            "Teaching Certificate Mathematics",
            "Teaching Certificate Mechanical and Process Engineering",
            "Teaching Certificate Physics",
            "Teaching Diploma Biology",
            "Teaching Diploma Chemistry",
            "Teaching Diploma Computer Science",
            "Teaching Diploma Geography",
            "Teaching Diploma Mathematics",
            "Teaching Diploma Physics",
            "Teaching Diploma Sport",
        ),
    ),
    DegreeLevelEntry(
        level=DegreeLevel.OTHER,
        name="Other",
        programmes=(
            "Exchange student",
            "ETH staff",
            "Other",
        ),
    ),
)
"""One entry per `DegreeLevel`, in the order of the enum."""

_PROGRAMMES: Final[Mapping[DegreeLevel, frozenset[str]]] = {
    entry.level: frozenset(entry.programmes) for entry in CATALOGUE
}


def is_programme(level: DegreeLevel, programme: str) -> bool:
    """Whether `programme` is a label of `level`, character for character."""
    return programme in _PROGRAMMES[level]
