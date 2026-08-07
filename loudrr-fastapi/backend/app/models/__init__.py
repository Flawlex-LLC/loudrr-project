# The one place every database model is registered. These imports are NEEDED
# for their side effect (loading each model so its table is attached to
# Base.metadata); ruff's F401 doesn't understand SQLAlchemy registration so
# they're explicitly silenced. If you remove one of these, the missing table
# won't be in create_all / migrations.
import app.models.user  # noqa: F401
import app.models.site_setting  # noqa: F401
import app.models.transaction  # noqa: F401
import app.models.waitlist_entry  # noqa: F401
import app.models.x_profile  # noqa: F401
import app.models.x_verification_request  # noqa: F401
import app.models.x_oauth_state  # noqa: F401
import app.models.post  # noqa: F401
import app.models.engagement  # noqa: F401
import app.models.verification_batch  # noqa: F401
import app.models.outbox_event  # noqa: F401
import app.models.feature_interest  # noqa: F401
import app.models.audit_log  # noqa: F401
import app.models.xp_transaction  # noqa: F401