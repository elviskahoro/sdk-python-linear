from datetime import date
import gtm_linear
from poc.ariadne.linear_ariadne import input_types as A

# current SDK
gtm_linear.IssueCreateInput(team_id="t", title="x", due_date=date(2026, 1, 1))
gtm_linear.IssueCreateInput(team_id="t", titel="typo")
gtm_linear.IssueCreateInput(title="missing team")
# ariadne (per-field Field(alias=...))
A.IssueCreateInput(team_id="t", title="x", due_date=date(2026, 1, 1))
A.IssueCreateInput(teamId="t", title="x")
A.IssueCreateInput(team_id="t", titel="typo")
A.IssueCreateInput(title="missing team")
reveal_type(A.IssueCreateInput)
reveal_type(gtm_linear.IssueCreateInput.__init__)
reveal_type(A.IssueCreateInput.__init__)
