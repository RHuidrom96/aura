# Aura Evaluation Platform: Incentives & Star Pathway Upgrades

This document outlines the design, implementation, and verification flows for the newly added **AURA Incentives & Star Pathway** features in the Aura Evaluation system.

---

## 1. Feature Overview

To improve annotator retention, confirm demographic profiles for academic authenticity, and award top performers, the following flows have been introduced:

### A. Campaign IDs
* **Monospace Identifiers**: Campaign IDs are displayed alongside campaign names on Dashboard cards, Campaign Detail pages, Configuration edit panels, and Results headers to facilitate quick team and annotator communications.

### B. Incentives Configuration (Admin Creation/Edit Forms)
Admins can toggle two types of incentives:
1. **Co-authorship**: Flag indicating that top annotators are eligible for publication acknowledgments.
2. **Monetary Reward**: Configurable variables including:
   * **Minimum Wage**: Base payment amount awarded per rated segment.
   * **Target completed tasks**: Number of segments required to qualify for payouts or stars.
   * **Level Multiplier ($L$)**: Factor representing task complexity.
   * **Intensity Multiplier ($I$)**: Factor representing annotator load.
   * **Star Bonus**: Financial reward given to annotators who earn a ⭐ Star.

### C. Background Details: Personal Info (Annotator Workspace)
Annotators working on a campaign with active monetary incentives see a **Campaign Incentives** banner.
* To unlock bonuses, the annotator must submit their demographic background details (**Personal Info**):
  * **Location** (City, Village, State)
  * **Parents' native language(s)**
  * **Stayed outside city/village?** (Yes/No)
  * **Duration outside** (if yes)
  * **Purpose of stay** (if yes)
  * **Exposure to other languages/environments**
* This is filled out via an overlay form directly on the rating screen without page reloads, ensuring zero disruption to work.

### D. Progress Monitoring & Evaluation (Admin Console)
Admins tracking a campaign can inspect and grade annotators via the **Progress** list:
1. **View Info**: Admins can view the submitted Personal Info demographics of any annotator.
2. **Evaluate Dialog**: A manual rating modal that allows the admin to:
   * Select a **Quality Rating** from 1 to 5 stars (⭐⭐⭐⭐⭐) to grade translation performance.
   * Manually toggle the Special ⭐ Star badge to award the configured bonus and scientific acknowledgement.

### E. Campaign Details Page & KPI Dashboard
The admin Campaign Details page has been redesigned to offer a premium dashboard aesthetic:
* **Typography**: Integrates the premium site fonts (`Hanken Grotesk` and `Libre Caslon Text`) for clean, balanced hierarchies.
* **KPI Metrics Grid**: Features a dynamic metrics row at the top showing:
  * **Annotators Enrolled**: Total registered study members.
  * **Completed Ratings**: Members who have annotated the complete study set.
  * **Total Rated Segments**: Total sum of segments evaluated.
* **Sleek Cards**: Configured with polished border radii and shadow elevations.

### F. Results & Scientific Acknowledgements
* **Starred Annotators**: Displayed with a ⭐ icon on results views and offline HTML reports.
* **Scientific Acknowledgements Card**: A dedicated section dynamically rendering names ofStarred annotators, highlighting their contributions to the study.

---

## 2. Technical Design & Models

### Database Schema Upgrades
A new mapping model `CampaignAnnotator` and several columns on `Campaign` have been added in `models.py`:

```python
class Campaign(db.Model):
    # ...
    incentive_co_authorship = db.Column(db.Boolean, default=False)
    incentive_money = db.Column(db.Boolean, default=False)
    incentive_min_wage = db.Column(db.Float, default=0.0)
    incentive_target_tasks = db.Column(db.Integer, default=10)
    incentive_level = db.Column(db.Float, default=1.0)
    incentive_intensity = db.Column(db.Float, default=1.0)
    incentive_bonus_amount = db.Column(db.Float, default=0.0)

class CampaignAnnotator(db.Model):
    __tablename__ = "campaign_annotators"
    id = db.Column(db.String(32), primary_key=True, default=_uuid)
    campaign_id = db.Column(db.String(32), db.ForeignKey("campaigns.id"), nullable=False)
    annotator_id = db.Column(db.String(32), db.ForeignKey("annotators.id"), nullable=False)
    
    # Form B Fields
    form_submitted = db.Column(db.Boolean, default=False)
    location = db.Column(db.String(255))
    parents_language = db.Column(db.String(255))
    stayed_outside = db.Column(db.Boolean, default=False)
    stayed_outside_duration = db.Column(db.String(100))
    stayed_outside_purpose = db.Column(db.String(255))
    exposure = db.Column(db.Text)
    
    # Grading & Rewards
    quality_score = db.Column(db.Float, default=100.0)
    has_star = db.Column(db.Boolean, default=False)
```

---

## 3. How to Test & Verify

For convenience, a populated demo campaign is available. Follow these steps:

### Testing the Admin View
1. Log in to the administrator portal at `http://localhost:5000/admin/login` using:
   * **Email**: `@gmail.com`
   * **Password**: ``
2. Open the campaign **`[DEMO] Northeast India MT Evaluation with Incentives`**.
3. Under the **Progress** table:
   * Click **View Info** next to `Demo Annotator` to see their submitted Personal Info details.
   * Click **Evaluate** next to `Demo Annotator` to open the evaluation modal, rate them using the stars widget, check **Award Special AURA Star & Bonus**, and save.
4. Click **Results** at the top of the campaign view to verify that a ⭐ is shown in the results spreadsheet and dynamic **Scientific Acknowledgements** card.

### Testing the Annotator View
1. Navigate directly to the study link: `http://localhost:5000/campaign/4a83ac15fd664af182e8eb4ee8663a58`.
2. Inspect the **🎁 Incentives** card on the landing page.
3. Log in using the annotator account:
   * **Email**: `demo_annotator@aura.edu`
   * **Password**: `demo123`
4. Inspect the green submission success status or view the background information modal.
