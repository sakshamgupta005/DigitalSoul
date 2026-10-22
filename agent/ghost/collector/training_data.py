"""Training Dataset Builder & Representative Data Generator for VLM Workflow Grounding.

Exports episodic visual memory into structured Vision-Language Model (VLM) training samples,
including normalized action coordinates, bounding boxes, OCR tokens, and representative screenshots.
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    Image = None

from agent.ghost.collector.visual_annotator import VisualAnnotator


@dataclass
class TrainingSample:
    sample_id: str
    workflow_name: str
    step_index: int
    app: str
    window_title: str
    action_type: str
    target_label: str
    coords_pixel: Optional[List[float]]
    coords_norm: Optional[List[float]]
    bbox_pixel: Optional[List[float]]
    bbox_norm: Optional[List[float]]
    instruction_prompt: str
    intent_description: str
    raw_screenshot_path: str
    marked_screenshot_path: str
    focus_crop_path: str
    ocr_tokens: List[Dict[str, Any]]
    created_at: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class TrainingDatasetExporter:
    """Exports visual memories into standard JSONL VLM dataset format."""

    def __init__(self, output_dir: Optional[Union[str, Path]] = None):
        self.output_dir = Path(output_dir) if output_dir else Path(".ghost/training_dataset")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.samples_file = self.output_dir / "samples.jsonl"

    def export_sample(self, sample: TrainingSample) -> None:
        """Appends a training sample to the JSONL dataset."""
        with open(self.samples_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(sample.to_dict(), ensure_ascii=False) + "\n")

    def export_batch(self, samples: List[TrainingSample]) -> int:
        """Batch export multiple samples."""
        with open(self.samples_file, "a", encoding="utf-8") as f:
            for s in samples:
                f.write(json.dumps(s.to_dict(), ensure_ascii=False) + "\n")
        return len(samples)


class RepresentativeDataGenerator:
    """Generates crisp, high-fidelity representative training screenshots and workflows."""

    def __init__(self, output_dir: Optional[Union[str, Path]] = None):
        self.output_dir = Path(output_dir) if output_dir else Path(".ghost/training_dataset")
        self.images_dir = self.output_dir / "images"
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.annotator = VisualAnnotator()
        self.exporter = TrainingDatasetExporter(output_dir=self.output_dir)

    def generate_whatsapp_flow(self) -> List[TrainingSample]:
        """Generates representative training steps for WhatsApp Desktop messaging."""
        samples: List[TrainingSample] = []
        screen_w, screen_h = 1920, 1080

        # Create realistic WhatsApp desktop base canvas
        def make_whatsapp_screen(active_chat: str = "Saksham Gupta", message: str = "Hello from DigitalSoul"):
            img = Image.new("RGB", (screen_w, screen_h), color=(17, 24, 30))
            draw = ImageDraw.Draw(img)

            # Left Sidebar (Chats list)
            draw.rectangle([100, 80, 480, 1000], fill=(24, 34, 42), outline=(38, 50, 60), width=1)
            # Search Box
            draw.rounded_rectangle([120, 100, 460, 136], radius=6, fill=(34, 46, 56), outline=(48, 62, 74))
            draw.text((140, 110), f"Search or start new chat", fill=(140, 155, 170))

            # Chat item 1 (Active)
            draw.rectangle([100, 150, 480, 220], fill=(36, 52, 64))
            draw.ellipse([116, 162, 156, 202], fill=(0, 168, 132))  # WhatsApp green avatar
            draw.text((170, 166), active_chat, fill=(240, 245, 250))
            draw.text((170, 188), "Online", fill=(0, 168, 132))

            # Chat item 2
            draw.ellipse([116, 236, 156, 276], fill=(80, 100, 115))
            draw.text((170, 240), "Product Team", fill=(210, 220, 230))
            draw.text((170, 260), "Sprint retro at 4 PM", fill=(130, 145, 160))

            # Right Chat Area
            draw.rectangle([480, 80, 1400, 1000], fill=(13, 20, 26), outline=(38, 50, 60), width=1)
            # Chat Header
            draw.rectangle([480, 80, 1400, 140], fill=(24, 34, 42))
            draw.text((510, 100), active_chat, fill=(240, 245, 250))

            # Message bubble
            if message:
                draw.rounded_rectangle([920, 820, 1360, 870], radius=8, fill=(0, 92, 75))
                draw.text((940, 835), message, fill=(240, 245, 250))

            # Input bar
            draw.rectangle([480, 930, 1400, 1000], fill=(24, 34, 42))
            draw.rounded_rectangle([550, 945, 1320, 985], radius=8, fill=(38, 52, 64))
            draw.text((570, 955), "Type a message" if not message else message, fill=(220, 230, 240))

            # Send Button (Circle with arrow)
            send_btn_box = [1340, 945, 1380, 985]
            draw.ellipse(send_btn_box, fill=(0, 168, 132))
            draw.polygon([(1355, 955), (1372, 965), (1355, 975)], fill=(255, 255, 255))

            return img, send_btn_box

        # Step 1: Click Search Bar
        img1, _ = make_whatsapp_screen(message="")
        coords1 = (280.0, 118.0)
        box1 = (120.0, 100.0, 460.0, 136.0)
        sample1 = self._build_sample(
            img=img1,
            workflow="WhatsApp Messaging",
            step_idx=1,
            app="WhatsApp.exe",
            title="WhatsApp - Desktop",
            action="click",
            label="Search input",
            coords=coords1,
            bbox=box1,
            prompt="Click on the search box to find contact 'Saksham Gupta'.",
            intent="User initiated contact search in WhatsApp.",
        )
        samples.append(sample1)

        # Step 2: Click Contact Result
        img2, _ = make_whatsapp_screen(message="")
        coords2 = (290.0, 185.0)
        box2 = (100.0, 150.0, 480.0, 220.0)
        sample2 = self._build_sample(
            img=img2,
            workflow="WhatsApp Messaging",
            step_idx=2,
            app="WhatsApp.exe",
            title="WhatsApp - Search Results",
            action="click",
            label="Saksham Gupta Contact",
            coords=coords2,
            bbox=box2,
            prompt="Select contact 'Saksham Gupta' from the chat list.",
            intent="User selected the contact conversation.",
        )
        samples.append(sample2)

        # Step 3: Type message into edit field
        img3, _ = make_whatsapp_screen(message="Reviewing DigitalSoul models")
        coords3 = (750.0, 965.0)
        box3 = (550.0, 945.0, 1320.0, 985.0)
        sample3 = self._build_sample(
            img=img3,
            workflow="WhatsApp Messaging",
            step_idx=3,
            app="WhatsApp.exe",
            title="WhatsApp - Saksham Gupta",
            action="set_text",
            label="Message field",
            coords=coords3,
            bbox=box3,
            prompt="Type message 'Reviewing DigitalSoul models' into the chat input field.",
            intent="User drafted text message.",
        )
        samples.append(sample3)

        # Step 4: Click Send Button
        img4, send_box = make_whatsapp_screen(message="Reviewing DigitalSoul models")
        coords4 = (1360.0, 965.0)
        box4 = (float(send_box[0]), float(send_box[1]), float(send_box[2]), float(send_box[3]))
        sample4 = self._build_sample(
            img=img4,
            workflow="WhatsApp Messaging",
            step_idx=4,
            app="WhatsApp.exe",
            title="WhatsApp - Saksham Gupta",
            action="click",
            label="Send Button",
            coords=coords4,
            bbox=box4,
            prompt="Click the green Send button to dispatch the message.",
            intent="User sent the drafted WhatsApp message.",
        )
        samples.append(sample4)

        # Export samples to dataset
        self.exporter.export_batch(samples)
        return samples

    def generate_crm_flow(self) -> List[TrainingSample]:
        """Generates representative training steps for Acme CRM customer segmentation."""
        samples: List[TrainingSample] = []
        screen_w, screen_h = 1920, 1080

        def make_crm_screen(filter_applied: bool = True, selected_row: bool = True):
            img = Image.new("RGB", (screen_w, screen_h), color=(245, 247, 250))
            draw = ImageDraw.Draw(img)

            # Top Navbar
            draw.rectangle([0, 0, screen_w, 64], fill=(26, 38, 57))
            draw.text((32, 22), "Acme CRM Enterprise", fill=(255, 255, 255))
            draw.text((260, 24), "Customers", fill=(0, 200, 140))
            draw.text((360, 24), "Invoices", fill=(180, 195, 210))
            draw.text((450, 24), "Reports", fill=(180, 195, 210))

            # Filter Bar
            draw.rectangle([60, 90, 1860, 150], fill=(255, 255, 255), outline=(220, 225, 230))
            draw.text((80, 112), "Status Filter:", fill=(90, 100, 110))
            filter_pill_box = (170.0, 104.0, 310.0, 138.0)
            fill_pill = (235, 245, 240) if filter_applied else (240, 242, 245)
            draw.rounded_rectangle(filter_pill_box, radius=6, fill=fill_pill, outline=(0, 180, 120))
            draw.text((188, 112), "At-Risk Customers", fill=(0, 140, 90) if filter_applied else (70, 80, 90))

            # Table Header
            draw.rectangle([60, 170, 1860, 210], fill=(238, 242, 246))
            draw.text((80, 182), "ACCOUNT ID", fill=(100, 112, 125))
            draw.text((250, 182), "COMPANY NAME", fill=(100, 112, 125))
            draw.text((600, 182), "TIER", fill=(100, 112, 125))
            draw.text((850, 182), "OVERDUE DAYS", fill=(100, 112, 125))
            draw.text((1150, 182), "SEGMENT STATUS", fill=(100, 112, 125))
            draw.text((1500, 182), "ACTIONS", fill=(100, 112, 125))

            # Table Row 1 (Target row)
            row_fill = (235, 243, 255) if selected_row else (255, 255, 255)
            draw.rectangle([60, 210, 1860, 265], fill=row_fill, outline=(225, 230, 236))
            draw.text((80, 230), "ACC-1042", fill=(40, 50, 60))
            draw.text((250, 230), "CyberDyne Systems", fill=(20, 30, 40))
            draw.text((600, 230), "Enterprise Pro", fill=(70, 80, 90))
            draw.text((850, 230), "45 Days", fill=(210, 50, 50))
            draw.text((1150, 230), "Unsegmented", fill=(140, 110, 20))

            # Tag Action Button on Row 1
            tag_btn_box = (1500.0, 222.0, 1680.0, 254.0)
            draw.rounded_rectangle(tag_btn_box, radius=4, fill=(15, 100, 220))
            draw.text((1520, 230), "Tag High-Risk", fill=(255, 255, 255))

            # Table Row 2
            draw.rectangle([60, 265, 1860, 320], fill=(255, 255, 255), outline=(225, 230, 236))
            draw.text((80, 285), "ACC-1088", fill=(40, 50, 60))
            draw.text((250, 285), "Initech Corporation", fill=(20, 30, 40))
            draw.text((600, 285), "Standard", fill=(70, 80, 90))
            draw.text((850, 285), "32 Days", fill=(210, 50, 50))
            draw.text((1150, 285), "Unsegmented", fill=(140, 110, 20))

            # Bottom Action Bar
            draw.rectangle([0, 1010, screen_w, 1080], fill=(255, 255, 255), outline=(220, 225, 230))
            save_btn_box = (1680.0, 1025.0, 1840.0, 1065.0)
            draw.rounded_rectangle(save_btn_box, radius=6, fill=(0, 160, 100))
            draw.text((1710, 1038), "Save & Sync", fill=(255, 255, 255))

            return img, filter_pill_box, (80.0, 210.0, 1400.0, 265.0), tag_btn_box, save_btn_box

        # Step 1: Click Status Filter
        img1, pill_box, _, _, _ = make_crm_screen(filter_applied=False)
        coords1 = ((pill_box[0] + pill_box[2]) / 2.0, (pill_box[1] + pill_box[3]) / 2.0)
        sample1 = self._build_sample(
            img=img1,
            workflow="CRM Customer Segmentation",
            step_idx=1,
            app="chrome.exe",
            title="Acme CRM - Customer Directory",
            action="click",
            label="At-Risk Filter",
            coords=coords1,
            bbox=pill_box,
            prompt="Filter the customer list by clicking 'At-Risk Customers'.",
            intent="User applied the At-Risk filter in Acme CRM.",
        )
        samples.append(sample1)

        # Step 2: Select Row CyberDyne
        img2, _, row_box, _, _ = make_crm_screen(filter_applied=True, selected_row=False)
        coords2 = (450.0, 238.0)
        sample2 = self._build_sample(
            img=img2,
            workflow="CRM Customer Segmentation",
            step_idx=2,
            app="chrome.exe",
            title="Acme CRM - Filtered Directory",
            action="click",
            label="CyberDyne Systems Row",
            coords=coords2,
            bbox=row_box,
            prompt="Click on the account row for 'CyberDyne Systems' to select it.",
            intent="User selected overdue account ACC-1042.",
        )
        samples.append(sample2)

        # Step 3: Click Tag High-Risk Button
        img3, _, _, tag_box, _ = make_crm_screen(filter_applied=True, selected_row=True)
        coords3 = ((tag_box[0] + tag_box[2]) / 2.0, (tag_box[1] + tag_box[3]) / 2.0)
        sample3 = self._build_sample(
            img=img3,
            workflow="CRM Customer Segmentation",
            step_idx=3,
            app="chrome.exe",
            title="Acme CRM - Customer Details",
            action="click",
            label="Tag High-Risk",
            coords=coords3,
            bbox=tag_box,
            prompt="Click the 'Tag High-Risk' button to classify the selected customer.",
            intent="User applied high-risk segmentation tag to account.",
        )
        samples.append(sample3)

        # Step 4: Click Save & Sync
        img4, _, _, _, save_box = make_crm_screen(filter_applied=True, selected_row=True)
        coords4 = ((save_box[0] + save_box[2]) / 2.0, (save_box[1] + save_box[3]) / 2.0)
        sample4 = self._build_sample(
            img=img4,
            workflow="CRM Customer Segmentation",
            step_idx=4,
            app="chrome.exe",
            title="Acme CRM - Pending Sync",
            action="click",
            label="Save & Sync",
            coords=coords4,
            bbox=save_box,
            prompt="Click the green 'Save & Sync' button to persist segmentation changes.",
            intent="User submitted and synced customer tags.",
        )
        samples.append(sample4)

        self.exporter.export_batch(samples)
        return samples

    def generate_all_representative_data(self) -> Dict[str, List[TrainingSample]]:
        """Generates a complete multi-domain representative training dataset."""
        return {
            "whatsapp": self.generate_whatsapp_flow(),
            "crm": self.generate_crm_flow(),
        }

    def _build_sample(
        self,
        img: Image.Image,
        workflow: str,
        step_idx: int,
        app: str,
        title: str,
        action: str,
        label: str,
        coords: Tuple[float, float],
        bbox: Tuple[float, float, float, float],
        prompt: str,
        intent: str,
    ) -> TrainingSample:
        sample_id = f"samp_{uuid.uuid4().hex[:10]}"
        w, h = img.size

        # 1. Save pristine raw image
        raw_path = self.images_dir / f"{sample_id}_raw.png"
        img.save(raw_path, format="PNG")

        # 2. Generate and save visually marked screenshot
        marked_img = self.annotator.annotate_action(
            image=img,
            coords=coords,
            bbox=bbox,
            action_kind=action,
            label=label,
        )
        marked_path = self.images_dir / f"{sample_id}_marked.png"
        marked_img.save(marked_path, format="PNG")

        # 3. Generate high-resolution localized focus crop
        crop_img = self.annotator.create_focus_crop(
            image=img,
            target_point=coords,
            bbox=bbox,
            crop_size=(520, 360),
        )
        crop_path = self.images_dir / f"{sample_id}_crop.png"
        crop_img.save(crop_path, format="PNG")

        # Normalize coordinates
        coords_norm = [round(coords[0] / w, 4), round(coords[1] / h, 4)]
        bbox_norm = [
            round(bbox[0] / w, 4),
            round(bbox[1] / h, 4),
            round(bbox[2] / w, 4),
            round(bbox[3] / h, 4),
        ]

        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        return TrainingSample(
            sample_id=sample_id,
            workflow_name=workflow,
            step_index=step_idx,
            app=app,
            window_title=title,
            action_type=action,
            target_label=label,
            coords_pixel=[coords[0], coords[1]],
            coords_norm=coords_norm,
            bbox_pixel=[bbox[0], bbox[1], bbox[2], bbox[3]],
            bbox_norm=bbox_norm,
            instruction_prompt=prompt,
            intent_description=intent,
            raw_screenshot_path=str(raw_path),
            marked_screenshot_path=str(marked_path),
            focus_crop_path=str(crop_path),
            ocr_tokens=[{"text": label, "box": {"x": bbox[0], "y": bbox[1], "width": bbox[2] - bbox[0], "height": bbox[3] - bbox[1]}}],
            created_at=now_iso,
        )
