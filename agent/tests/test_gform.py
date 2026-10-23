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
    mem_file = tmp_path / "test_user_memory.md"
    mem = GFormMemory(mem_file)
    # Memory starts empty to avoid fake defaults
    assert mem.profile == {}

    # Seed genuine fact learned from the user
    mem.save({"name": "Sahil", "gender": "Male"})
    assert mem.profile["name"] == "Sahil"

    # Match name
    key, val, conf = mem.match_question("What is your Full Name?")
    assert key == "name"
    assert val == "Sahil"
    assert conf >= 0.75

    # Save new field
    mem.save({"favorite_color": "Blue"})
    assert mem.profile["favorite_color"] == "Blue"

    # Reload memory from disk
    reloaded = GFormMemory(mem_file)
    assert reloaded.profile["favorite_color"] == "Blue"
    assert reloaded.profile["name"] == "Sahil"

    # Verify user_name synonym matching
    syn_mem = GFormMemory(tmp_path / "syn_mem.md")
    syn_mem.save({"user_name": "Sahil GNDU", "college": "GNDU University"})
    s_key, s_val, s_conf = syn_mem.match_question("Full Name")
    assert s_val == "Sahil GNDU"
    o_key, o_val, o_conf = syn_mem.match_question("Organization / College Name")
    assert o_val == "GNDU University"


def test_extract_form_structure():
    title, questions = extract_form_structure(SAMPLE_HTML)
    assert title == "Community Enrolment Form"
    assert len(questions) == 4
    assert questions[0]["title"] == "Your Full Name"
    assert questions[0]["type"] == 0
    assert questions[1]["title"] == "Gender"
    assert questions[1]["options"] == ["Female", "Male", "Other"]


def test_form_mapping_known_vs_new(tmp_path):
    mem_file = tmp_path / "user_memory.md"
    mem = GFormMemory(mem_file)
    mem.save({"name": "Sahil", "gender": "Male"})
    title, questions = extract_form_structure(SAMPLE_HTML)

    known = []
    new = []
    for q in questions:
        key, val, conf = mem.match_question(q["title"], q.get("options"))
        if val and conf >= 0.7:
            known.append((q["title"], val))
        else:
            new.append(q["title"])

    assert len(known) == 2  # Name, Gender
    assert "Favorite Color" in new  # Unanswered new field


def test_google_cloud_enrolment_form_inspection(tmp_path):
    url = "https://docs.google.com/forms/d/e/1FAIpQLScIxD14Pyc183PbDLHcS9N-nbQRRYSncqY0TP3g7qSsfWgnKQ/viewform"
    mem = GFormMemory(tmp_path / "user_memory.md")
    mem.save({"name": "Sahil", "email": "sahil@example.com"})
    res = GFormActor.inspect_url(url, memory=mem)
    assert res["title"] != ""
    assert res["total_questions"] > 0
    assert len(res["known_fields"]) >= 2
    assert any(f["matched_key"] == "email" for f in res["known_fields"])
    assert any(f["matched_key"] == "name" for f in res["known_fields"])

