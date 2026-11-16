"""Read-only website help, kept inside the removable AI package."""

from pathlib import Path
from typing import Literal


GuideTopic = Literal[
    "overview",
    "animal_ids",
    "add_animals",
    "edit_animals",
    "genetics",
    "parentage",
    "procedures",
    "projects",
    "recordings",
    "files_and_images",
    "spreadsheet_import",
    "reports",
    "sharing",
    "settings",
    "flare_ai_guide",
    "all",
]

GUIDE_PATH = Path(__file__).resolve().parent.parent / "user_guide.md"


def user_guide(topic: GuideTopic = "overview") -> dict:
    """Explain how to use FLARE-ELN, with actual button names and form steps.

    FLARE-ELN is species-generic; use animals unless a specific species is known.
    Use for website help, field meanings, Animal ID versus local identifier,
    adding/editing animals, genotypes, parents, procedures, projects, recordings,
    files/images, spreadsheet imports, reports, sharing and settings.
    For received codes, share links, or the Enter code button, use topic='sharing'.
    For technical information about how the Flare-AI assistant work, use  topic='flare_ai_guide'.
    This returns documentation, not live laboratory records, and saves nothing.

    Args:
        topic: Fetch the relevant section. overview lists the available topics;
            all returns the complete manual and should only be used when needed.
    """
    guide = GUIDE_PATH.read_text(encoding="utf-8")
    sections = {}
    for section in guide.split("\n## ")[1:]:
        name, _, content = section.partition("\n")
        sections[name.strip()] = content.strip()

    if topic != "all" and topic not in sections:
        raise ValueError(
            f"Unknown guide topic: {topic}. Choose from {', '.join(sections)} or all."
        )

    return {
        "topic": topic,
        "content": guide if topic == "all" else sections[topic],
        "available_topics": {
            name: content.splitlines()[0].removeprefix("### ")
            for name, content in sections.items()
        },
        "source": "flare_ai/user_guide.md" + (f"#{topic}" if topic != "all" else ""),
    }
