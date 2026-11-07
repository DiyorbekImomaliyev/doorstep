"""RG-03 Ragas eval. Ragas sends usage analytics unless RAGAS_DO_NOT_TRACK is "true"; it is set
here, before Ragas is imported, so no eval run reports anything to Ragas."""

import os

os.environ["RAGAS_DO_NOT_TRACK"] = "true"
