"""
conversational_ui.py

This module provides the conversational UI and UX logic for the AI ERP assistant.
It handles user interactions, generates short actionable replies, asks clarifying questions, and offers suggestions.
"""

class ConversationalUI:
    def __init__(self, ai_agent_core):
        self.ai_agent = ai_agent_core

    def get_user_input(self, prompt):
        """Simulate getting user input (to be replaced with real UI integration)."""
        # TODO: Integrate with web/app/chat interface
        return input(prompt)

    def send_response(self, response):
        """Send a response to the user (to be replaced with real UI integration)."""
        # TODO: Integrate with web/app/chat interface
        print(response)

    def ask_clarifying_question(self, question, options=None):
        """Ask a clarifying question with optional clickable suggestions."""
        # TODO: Integrate with UI for clickable options
        if options:
            print(f"{question} Options: {', '.join(options)}")
        else:
            print(question)
        return self.get_user_input("Your answer: ")

    def interact(self, user_context):
        """Main interaction loop (to be replaced with real event-driven UI)."""
        while True:
            user_query = self.get_user_input("How can I help you? ")
            if user_query.lower() in ("exit", "quit"):
                self.send_response("Goodbye!")
                break
            response = self.ai_agent.handle_query(user_query, user_context)
            self.send_response(response)

# Usage example:
# from .ai_agent_core import AIAgentCore
# db_conn = ...
# ai_agent = AIAgentCore(db_conn)
# ui = ConversationalUI(ai_agent)
# ui.interact(user_context)
