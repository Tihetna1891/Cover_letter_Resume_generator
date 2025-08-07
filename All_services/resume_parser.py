import spacy
import re
from typing import Dict, List, Optional

nlp = spacy.load("en_core_web_sm")

class ResumeParser:
    @staticmethod
    def parse_name(text: str) -> Optional[str]:
        first_line = text.split('\n')[0].strip()
        if "@" not in first_line and not any(c.isdigit() for c in first_line):
            return first_line
        return None

    @staticmethod
    def parse_contact(text: str) -> Dict:
        email = re.search(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", text)
        phone = re.search(
            r"(\+?\d{1,2}\s?)?(\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4})",
            text
        )
        return {
            "email": email.group(0) if email else "",
            "phone": phone.group(0) if phone else ""
        }

    @staticmethod
    def parse_location(text: str) -> Optional[str]:
        doc = nlp(text)
        locations = [ent.text for ent in doc.ents if ent.label_ == "GPE"]
        return locations[0] if locations else None

    @staticmethod
    def parse_education(text: str) -> List[Dict]:
        education = []
        edu_pattern = r"(?i)(education.*?)(?=work experience|$)"
        if match := re.search(edu_pattern, text, re.DOTALL):
            edu_text = match.group(1)
            doc = nlp(edu_text)
            current_edu = {}
            for sent in doc.sents:
                if any(word in sent.text.lower() for word in ["university", "college"]):
                    if current_edu:
                        education.append(current_edu)
                    current_edu = {
                        "institution": next((ent.text for ent in sent.ents if ent.label_ == "ORG"), sent.text.split(",")[0]),
                        "degree": " ".join([token.text for token in sent if token.pos_ in ("NOUN", "PROPN") and token.text.lower() not in ["university", "college"]]),
                        "graduation_date": next((ent.text for ent in sent.ents if ent.label_ == "DATE"), ""),
                        "gpa": next((m.group(0) for m in re.finditer(r"\b\d\.\d{1,2}\b", sent.text)), None)
                    }
            if current_edu:
                education.append(current_edu)
        return education

    @staticmethod
    def parse_experience(text: str) -> List[Dict]:
        experience = []
        exp_pattern = r"(?i)(work experience|experience.*?)(?=education|skills|$)"
        if match := re.search(exp_pattern, text, re.DOTALL):
            exp_text = match.group(1)
            doc = nlp(exp_text)
            current_exp = {}
            for sent in doc.sents:
                if any(word in sent.text.lower() for word in ["company", "inc", "llc", "intern"]):
                    if current_exp:
                        experience.append(current_exp)
                    current_exp = {
                        "company": next((ent.text for ent in sent.ents if ent.label_ == "ORG"), sent.text.split(",")[0]),
                        "position": " ".join([token.text for token in sent if token.dep_ in ("compound", "amod") or token.pos_ == "NOUN"][:4]),
                        "duration": next((ent.text for ent in sent.ents if ent.label_ == "DATE"), ""),
                        "location": next((ent.text for ent in sent.ents if ent.label_ == "GPE"), None)
                    }
            if current_exp:
                experience.append(current_exp)
        return experience