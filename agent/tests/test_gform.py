import pytest
from pathlib import Path
from agent.ghost.browser.gform_memory import GFormMemory, DEFAULT_PROFILE
from agent.ghost.browser.gform_actor import extract_form_structure, GFormActor


SAMPLE_HTML = """
<!DOCTYPE html><html><head><title>Community Enrolment Form - Google Forms</title></head>
<body>
<script>
var FB_PUBLIC_LOAD_DATA_ = [null, [null, [
  [null, "Your Full Name", null, 0, [[1001, null, null]]],
  [null, "Gender", null, 2, [[1002, [["Female"], ["Male"], ["Other"]], null]]],
  [null, "I have read and accept the terms", null, 2, [[1003, [["Yes, I accept the terms"], ["No, decline"]], null]]],
  [null, "Favorite Color", null, 0, [[1004, null, null]]]
], null, null, null, null, null, "Community Enrolment Form"]];
</script>
</body></html>
"""


def test_gform_memory_defaults_and_matching(tmp_path):
    mem_file = tmp_path / "test_profile.json"
    mem = GFormMemory(mem_file)
    assert mem.profile["name"] == DEFAULT_PROFILE["name"]

    # Match name
    key, val, conf = mem.match_question("What is your Full Name?")
    assert key == "name"
    assert val == DEFAULT_PROFILE["name"]
    assert conf >= 0.75

    # Match positive agreement
    key, val, conf = mem.match_question("I have read and accept the terms", ["Yes, I accept the terms", "No, decline"])
    assert key == "agreement"
    assert val == "Yes, I accept the terms"
    assert conf >= 0.9

    # Save new field
    mem.save({"favorite_color": "Blue"})
    assert mem.profile["favorite_color"] == "Blue"

    # Reload memory
    reloaded = GFormMemory(mem_file)
    assert reloaded.profile["favorite_color"] == "Blue"


def test_extract_form_structure():
    title, questions = extract_form_structure(SAMPLE_HTML)
    assert title == "Community Enrolment Form"
    assert len(questions) == 4
    assert questions[0]["title"] == "Your Full Name"
    assert questions[0]["type"] == 0
    assert questions[1]["title"] == "Gender"
    assert questions[1]["options"] == ["Female", "Male", "Other"]


def test_form_mapping_known_vs_new(tmp_path):
    mem = GFormMemory(tmp_path / "profile.json")
    title, questions = extract_form_structure(SAMPLE_HTML)
    
    known = []
    new = []
    for q in questions:
        key, val, conf = mem.match_question(q["title"], q.get("options"))
        if val and conf >= 0.7:
            known.append((q["title"], val))
        else:
            new.append(q["title"])

    assert len(known) >= 2  # Name, Gender, Agreement
    assert "Favorite Color" in new  # Unanswered new field
