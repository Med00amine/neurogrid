"""database-level safety triggers

These are the last line of defence if application code has a bug:

* outreach_events / audit_log / eligibility_decisions are append-only
* suppressions can never be deleted (only deactivated, with an audit trail)
* a live message cannot be queued/submitted to a suppressed recipient
* message status can only follow the allowed state machine (an `unknown` or
  `submitted` message can never go back to `queued`, so it can never be re-sent)
* the snapshot of what is sent (recipient/subject/body/ids) is immutable

Revision ID: 0002
Revises: 0001
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION forbid_mutation() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION '% is append-only: % is not allowed', TG_TABLE_NAME, TG_OP
            USING ERRCODE = 'integrity_constraint_violation';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for table in ("outreach_events", "audit_log", "eligibility_decisions"):
        op.execute(
            f"CREATE TRIGGER {table}_no_update_delete BEFORE UPDATE OR DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION forbid_mutation();"
        )
        op.execute(
            f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table} "
            f"FOR EACH STATEMENT EXECUTE FUNCTION forbid_mutation();"
        )
    # suppressions: rows may be inserted/deactivated but never deleted
    op.execute(
        "CREATE TRIGGER suppressions_no_delete BEFORE DELETE ON suppressions "
        "FOR EACH ROW EXECUTE FUNCTION forbid_mutation();"
    )
    op.execute(
        "CREATE TRIGGER suppressions_no_truncate BEFORE TRUNCATE ON suppressions "
        "FOR EACH STATEMENT EXECUTE FUNCTION forbid_mutation();"
    )

    op.execute(
        """
        CREATE FUNCTION messages_guard() RETURNS trigger AS $$
        BEGIN
          -- 1. never queue/submit a live message to a suppressed recipient
          IF NEW.is_dry_run = false AND NEW.status IN ('queued', 'submitting')
             AND (TG_OP = 'INSERT' OR OLD.status IS DISTINCT FROM NEW.status) THEN
            IF EXISTS (
              SELECT 1 FROM suppressions s
               WHERE s.active AND (
                 (s.kind = 'email'  AND s.value = lower(NEW.recipient_email)) OR
                 (s.kind = 'domain' AND s.value = lower(split_part(NEW.recipient_email, '@', 2))))
            ) THEN
              RAISE EXCEPTION 'recipient % is on the suppression list', NEW.recipient_email
                USING ERRCODE = 'check_violation';
            END IF;
          END IF;

          IF TG_OP = 'UPDATE' THEN
            -- 2. immutable snapshot of what is sent
            IF NEW.recipient_email IS DISTINCT FROM OLD.recipient_email
               OR NEW.from_email IS DISTINCT FROM OLD.from_email
               OR NEW.subject IS DISTINCT FROM OLD.subject
               OR NEW.body_text IS DISTINCT FROM OLD.body_text
               OR NEW.contact_id IS DISTINCT FROM OLD.contact_id
               OR NEW.campaign_id IS DISTINCT FROM OLD.campaign_id
               OR NEW.sequence_step IS DISTINCT FROM OLD.sequence_step
               OR NEW.is_dry_run IS DISTINCT FROM OLD.is_dry_run
               OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
               OR NEW.rfc_message_id IS DISTINCT FROM OLD.rfc_message_id THEN
              RAISE EXCEPTION 'message % is immutable once created', OLD.id
                USING ERRCODE = 'integrity_constraint_violation';
            END IF;

            -- 3. status state machine
            IF NEW.status IS DISTINCT FROM OLD.status THEN
              IF NOT (
                (OLD.status = 'queued'     AND NEW.status IN ('submitting', 'suppressed', 'cancelled', 'failed')) OR
                (OLD.status = 'submitting' AND NEW.status IN ('submitted', 'queued', 'rejected', 'failed', 'unknown')) OR
                (OLD.status = 'submitted'  AND NEW.status IN ('delivered', 'bounced', 'replied')) OR
                (OLD.status = 'delivered'  AND NEW.status IN ('bounced', 'replied')) OR
                (OLD.status = 'unknown'    AND NEW.status IN ('submitted', 'failed', 'delivered', 'bounced', 'replied'))
              ) THEN
                RAISE EXCEPTION 'illegal message status transition % -> %', OLD.status, NEW.status
                  USING ERRCODE = 'check_violation';
              END IF;
            END IF;
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER messages_guard_trg BEFORE INSERT OR UPDATE ON messages "
        "FOR EACH ROW EXECUTE FUNCTION messages_guard();"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS messages_guard_trg ON messages;")
    op.execute("DROP FUNCTION IF EXISTS messages_guard();")
    op.execute("DROP TRIGGER IF EXISTS suppressions_no_truncate ON suppressions;")
    op.execute("DROP TRIGGER IF EXISTS suppressions_no_delete ON suppressions;")
    for table in ("outreach_events", "audit_log", "eligibility_decisions"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_truncate ON {table};")
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_update_delete ON {table};")
    op.execute("DROP FUNCTION IF EXISTS forbid_mutation();")
