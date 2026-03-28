"""
test_ai_assistant.py

Basic integration test for AI ERP assistant modules.
"""


from ai_assistant.knowledge_library import KnowledgeLibrary
from ai_assistant.data_access import ERPDataAccess
from ai_assistant.live_data_validation import LiveDataValidator
from ai_assistant.ai_agent_core import AIAgentCore
from ai_assistant.crm_sales_analytics import CRMSalesAnalytics
from ai_assistant.financial_inventory_analytics import FinancialInventoryAnalytics
from ai_assistant.conversational_ui import ConversationalUI
from ai_assistant.todo_workflow_automation import ToDoWorkflowAutomation
from ai_assistant.continuous_learning import ContinuousLearning

class DummyDBConnection:
    pass  # Replace with mock or real connection as needed

def test_module_integration():
    db_conn = DummyDBConnection()
    knowledge = KnowledgeLibrary()
    data_access = ERPDataAccess(db_conn)
    validator = LiveDataValidator(db_conn, knowledge)
    ai_agent = AIAgentCore(db_conn)
    crm_analytics = CRMSalesAnalytics(db_conn, knowledge)
    fin_analytics = FinancialInventoryAnalytics(db_conn, knowledge)
    todo_automation = ToDoWorkflowAutomation(db_conn, knowledge)
    learning = ContinuousLearning(knowledge)
    ui = ConversationalUI(ai_agent)
    print("All modules instantiated successfully.")

if __name__ == "__main__":
    test_module_integration()
    print("Basic integration test completed.")
