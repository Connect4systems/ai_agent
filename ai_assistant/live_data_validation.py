"""
live_data_validation.py

This module provides the live data validation layer for the AI ERP assistant.
It ensures all responses are checked against the current ERP database before replying.
"""

from .data_access import ERPDataAccess
from .knowledge_library import KnowledgeLibrary

class LiveDataValidator:
    def __init__(self, db_connection, knowledge_library: KnowledgeLibrary):
        self.data_access = ERPDataAccess(db_connection)
        self.knowledge = knowledge_library

    def validate_and_refresh(self, key, *args, **kwargs):
        """Refresh the specified category from the live database and update the knowledge library."""
        fetch_method = getattr(self.data_access, f'get_{key}', None)
        if fetch_method:
            live_data = fetch_method(*args, **kwargs)
            self.knowledge.update(key, live_data)
            return live_data
        return None

    def validate_response(self, key, record_id):
        """Validate a specific record against the live database before responding."""
        fetch_method = getattr(self.data_access, f'get_{key}', None)
        if fetch_method:
            live_data = fetch_method()
            for record in live_data:
                if record.get('id') == record_id:
                    return record
        return None

    def validate_numeric_response_payload(self, payload):
        """
        Validate that numeric/financial response payload contains mandatory live query metadata.
        Required keys:
          - success
          - source
          - filters
          - computation
          - as_of
        """
        if not isinstance(payload, dict):
            return False, "Payload is not a dictionary."

        required = ["success", "source", "filters", "computation", "as_of"]
        missing = [k for k in required if k not in payload]
        if missing:
            return False, f"Missing required payload keys: {', '.join(missing)}"

        if not isinstance(payload.get("source"), list) or not payload.get("source"):
            return False, "Invalid or empty source list."

        if not isinstance(payload.get("filters"), dict):
            return False, "Invalid filters object."

        if not str(payload.get("computation") or "").strip():
            return False, "Missing computation description."

        if not str(payload.get("as_of") or "").strip():
            return False, "Missing as_of timestamp."

        return True, None

# Usage example:
# db_conn = ...
# knowledge = KnowledgeLibrary()
# validator = LiveDataValidator(db_conn, knowledge)
# validator.validate_and_refresh('doctypes')
# validator.validate_response('users', user_id)
