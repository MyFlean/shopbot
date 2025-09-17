"""
Centralized configuration for intent mappings, slot requirements, and questions.

This module serves as the single source of truth for:
- Intent classification mappings
- Required slots and functions per intent
- Question generation hints and fallbacks
- Category-specific hints
- Function TTLs and other configuration

Updated for simplified Elasticsearch-based architecture.
"""

from datetime import timedelta
from typing import Dict, List, Any

from .enums import QueryIntent, UserSlot, BackendFunction


# ─────────────────────────────────────────────────────────────
# Intent to QueryIntent and Suggested Requirements Mapping
# ─────────────────────────────────────────────────────────────
INTENT_MAPPING: Dict[str, Dict[str, Any]] = {
    # A1: Awareness & Discovery
    "Product_Discovery": {
        "query_intent": QueryIntent.PRODUCT_SEARCH,
        "suggested_slots": [UserSlot.USER_PREFERENCES, UserSlot.USER_BUDGET, UserSlot.PRODUCT_CATEGORY],
        "suggested_functions": [BackendFunction.SEARCH_PRODUCTS],
    },
    "Recommendation": {
        "query_intent": QueryIntent.RECOMMENDATION,
        "suggested_slots": [UserSlot.USER_PREFERENCES, UserSlot.USER_BUDGET, UserSlot.DIETARY_REQUIREMENTS],
        "suggested_functions": [
            BackendFunction.SEARCH_PRODUCTS,
            BackendFunction.FETCH_PURCHASE_HISTORY,
            BackendFunction.FETCH_USER_PROFILE,
        ],
    },
    
    # B1: Consideration - Catalogue
    "Specific_Product_Search": {
        "query_intent": QueryIntent.PRODUCT_SEARCH,
        "suggested_slots": [UserSlot.PRODUCT_CATEGORY],
        "suggested_functions": [BackendFunction.SEARCH_PRODUCTS],
    },
    "Product_Comparison": {
        "query_intent": QueryIntent.PRODUCT_COMPARISON,
        "suggested_slots": [UserSlot.USER_PREFERENCES, UserSlot.USER_BUDGET],
        "suggested_functions": [BackendFunction.SEARCH_PRODUCTS],
    },
    "Price_Inquiry": {
        "query_intent": QueryIntent.PRICE_INQUIRY,
        "suggested_slots": [],
        "suggested_functions": [BackendFunction.SEARCH_PRODUCTS],
    },
    
    # B2: Consideration - Logistics
    "Availability_Delivery_Inquiry": {
        "query_intent": QueryIntent.PRICE_INQUIRY,  # Reusing existing enum
        "suggested_slots": [UserSlot.DELIVERY_ADDRESS],
        "suggested_functions": [BackendFunction.SEARCH_PRODUCTS],
    },
    
    # C1: Transaction
    "Purchase_Checkout": {
        "query_intent": QueryIntent.PURCHASE,
        "suggested_slots": [
            UserSlot.QUANTITY,
            UserSlot.DELIVERY_ADDRESS,
        ],
        "suggested_functions": [
            BackendFunction.SEARCH_PRODUCTS,
            BackendFunction.FETCH_USER_PROFILE,
        ],
    },
    "Order_Modification": {
        "query_intent": QueryIntent.PURCHASE,
        "suggested_slots": [UserSlot.ORDER_ID],
        "suggested_functions": [BackendFunction.FETCH_PURCHASE_HISTORY],
    },
    
    # D1: Post-Purchase - Logistics
    "Order_Status": {
        "query_intent": QueryIntent.ORDER_STATUS,
        "suggested_slots": [UserSlot.ORDER_ID],
        "suggested_functions": [BackendFunction.FETCH_PURCHASE_HISTORY],
    },
    "Returns_Refunds": {
        "query_intent": QueryIntent.ORDER_STATUS,
        "suggested_slots": [UserSlot.ORDER_ID],
        "suggested_functions": [BackendFunction.FETCH_PURCHASE_HISTORY],
    },
    
    # D2: Post-Purchase - Engagement
    "Feedback_Review_Submission": {
        "query_intent": QueryIntent.GENERAL_HELP,
        "suggested_slots": [UserSlot.ORDER_ID],
        "suggested_functions": [BackendFunction.FETCH_PURCHASE_HISTORY],
    },
    "Subscription_Reorder": {
        "query_intent": QueryIntent.PURCHASE,
        "suggested_slots": [UserSlot.QUANTITY],
        "suggested_functions": [
            BackendFunction.FETCH_PURCHASE_HISTORY,
            BackendFunction.SEARCH_PRODUCTS,
        ],
    },
    
    # E1 & E2: Account & Support
   "Account_Profile_Management": {
       "query_intent": QueryIntent.GENERAL_HELP,
       "suggested_slots": [],
       "suggested_functions": [BackendFunction.FETCH_USER_PROFILE],
   },
   "Technical_Support": {
       "query_intent": QueryIntent.GENERAL_HELP,
       "suggested_slots": [],
       "suggested_functions": [],
   },
   "General_Help": {
       "query_intent": QueryIntent.GENERAL_HELP,
       "suggested_slots": [],
       "suggested_functions": [],
   },
}


# ─────────────────────────────────────────────────────────────
# Slot to Session Key Mapping
# ─────────────────────────────────────────────────────────────
SLOT_TO_SESSION_KEY: Dict[UserSlot, str] = {
   UserSlot.USER_PREFERENCES: "preferences",
   UserSlot.USER_BUDGET: "budget",
   UserSlot.DELIVERY_ADDRESS: "delivery_address",
   UserSlot.PRODUCT_CATEGORY: "product_category",
   UserSlot.DIETARY_REQUIREMENTS: "dietary_requirements",
   UserSlot.USE_CASE: "use_case",
   UserSlot.QUANTITY: "quantity",
   UserSlot.ORDER_ID: "order_id",
   # Personal care domain-specific
   UserSlot.PC_CONCERN: "pc_concern",
   UserSlot.PC_COMPATIBILITY: "pc_compatibility",
   UserSlot.INGREDIENT_AVOID: "ingredient_avoid",
}


# ─────────────────────────────────────────────────────────────
# Question Generation Hints and Fallbacks
# ─────────────────────────────────────────────────────────────
SLOT_QUESTIONS: Dict[UserSlot, Dict[str, Any]] = {
   UserSlot.USER_BUDGET: {
       "generation_hints": {
           "type": "budget_input",
           "should_include_options": True,
           "option_count": 3,
           "consider_factors": ["product_category", "price_range", "market_segment"],
           "adaptive": True,
       },
       "fallback": {
           "message": "What's your budget range?",
           "type": "multi_choice",
           "options": ["Under ₹100", "₹100-500", "Over ₹500"],
       }
   },
   UserSlot.USER_PREFERENCES: {
       "generation_hints": {
           "type": "preferences_input",
           "should_include_options": True,
           "option_count": 3,
           "consider_factors": ["product_type", "use_case", "category_specific_features"],
           "allow_multiple": False,
       },
       "fallback": {
           "message": "What features matter most to you?",
           "type": "multi_choice",
           "options": ["Quality", "Brand reputation", "Value for money"],
           "hints": ["Consider size, brand, quality, features, etc."],
       }
   },
   UserSlot.PRODUCT_CATEGORY: {
       "generation_hints": {
           "type": "category_input",
           "should_include_options": True,
           "option_count": 3,
           "consider_factors": ["user_query", "context"],
       },
       "fallback": {
           "message": "What type of product are you looking for?",
           "type": "multi_choice",
           "options": ["Food & Beverages", "Health & Nutrition", "Personal Care"],
       }
   },
   UserSlot.DIETARY_REQUIREMENTS: {
       "generation_hints": {
           "type": "dietary_input",
           "should_include_options": True,
           "option_count": 3,
           "consider_factors": ["health_goals", "restrictions"],
       },
       "fallback": {
           "message": "Do you have any dietary requirements?",
           "type": "multi_choice",
           "options": ["Gluten Free", "Vegan", "No restrictions"],
       }
   },
   UserSlot.DELIVERY_ADDRESS: {
       "generation_hints": {
           "type": "address_input",
           "check_saved_addresses": True,
           "include_landmarks": True,
           "validate_pincode": True,
       },
       "fallback": {
           "message": "What's your delivery address?",
           "type": "text_input",
           "placeholder": "Enter your full address with PIN code",
       }
   },
   UserSlot.USE_CASE: {
       "generation_hints": {
           "type": "text_input",
           "provide_examples": True,
           "context_specific": True,
       },
       "fallback": {
           "message": "What will you be using this for?",
           "type": "multi_choice",
           "options": ["Personal use", "Gift", "Daily consumption"],
       }
   },
   UserSlot.QUANTITY: {
       "generation_hints": {
           "type": "quantity_input",
           "suggest_bulk_discounts": True,
           "show_stock_info": True,
           "default_value": 1,
       },
       "fallback": {
           "message": "How many would you like?",
           "type": "multi_choice",
           "options": ["1", "2-5", "Bulk order"],
       }
   },
   UserSlot.ORDER_ID: {
       "generation_hints": {
           "type": "order_id_input",
           "show_recent_orders": True,
           "validate_format": True,
       },
       "fallback": {
           "message": "What's your order ID?",
           "type": "text_input",
           "placeholder": "e.g., ORD-12345-67890",
       }
   },
}


# ─────────────────────────────────────────────────────────────
# Category-Specific Question Hints
# ─────────────────────────────────────────────────────────────
CATEGORY_QUESTION_HINTS = {
   "f_and_b": {
       "budget_ranges": ["Under ₹100", "₹100-500", "₹500-1000", "Over ₹1000"],
       "preference_options": ["Taste", "Brand", "Organic", "Nutritional value", "Package size", "Shelf life"],
       "common_use_cases": ["Daily consumption", "Special occasions", "Health goals", "Family pack"],
   },
   "health_nutrition": {
       "budget_ranges": ["Under ₹500", "₹500-1500", "₹1500-3000", "Over ₹3000"],
       "preference_options": ["Effectiveness", "Brand reputation", "Natural ingredients", "Doctor recommended", "No side effects"],
       "common_use_cases": ["General wellness", "Specific health issue", "Fitness goals", "Medical need"],
   },
   "personal_care": {
       "budget_ranges": ["Under ₹200", "₹200-800", "₹800-2000", "Over ₹2000"],
       "preference_options": ["Skin type", "Brand", "Natural ingredients", "Fragrance", "Dermatologist tested"],
       "common_use_cases": ["Daily use", "Special occasions", "Sensitive skin", "Travel size"],
   },
   "general": {
       "budget_ranges": ["Low", "Medium", "High", "Premium"],
       "preference_options": ["Quality", "Brand", "Features", "Value for money", "Durability"],
       "common_use_cases": ["Personal use", "Gift", "Business", "Other"],
   }
}


# ─────────────────────────────────────────────────────────────
# Function TTL Configuration (Simplified)
# ─────────────────────────────────────────────────────────────
FUNCTION_TTL: Dict[BackendFunction, timedelta] = {
   BackendFunction.SEARCH_PRODUCTS: timedelta(minutes=5),
   BackendFunction.FETCH_USER_PROFILE: timedelta(minutes=15),
   BackendFunction.FETCH_PURCHASE_HISTORY: timedelta(minutes=10),
   BackendFunction.FETCH_ORDER_STATUS: timedelta(minutes=5),
}


# ─────────────────────────────────────────────────────────────
# Validation Functions
# ─────────────────────────────────────────────────────────────
def validate_config() -> List[str]:
   """Validate configuration integrity. Returns list of errors."""
   errors = []
   
   # Check all intents have valid query_intent
   for intent, config in INTENT_MAPPING.items():
       if "query_intent" not in config:
           errors.append(f"{intent} missing query_intent")
       elif not isinstance(config["query_intent"], QueryIntent):
           errors.append(f"{intent} has invalid query_intent type")
   
   # Check all suggested slots have questions
   for intent, config in INTENT_MAPPING.items():
       for slot in config.get("suggested_slots", []):
           if slot not in SLOT_QUESTIONS:
               errors.append(f"{intent} suggests {slot.value} but no question config exists")
           else:
               slot_config = SLOT_QUESTIONS[slot]
               if "generation_hints" not in slot_config and "fallback" not in slot_config:
                   errors.append(f"{slot.value} has neither generation_hints nor fallback")
           if slot not in SLOT_TO_SESSION_KEY:
               errors.append(f"{intent} suggests {slot.value} but no session key mapping exists")
   
   # Check all slots in questions have session keys
   for slot in SLOT_QUESTIONS:
       if slot not in SLOT_TO_SESSION_KEY:
           errors.append(f"Question config exists for {slot.value} but no session key mapping")
   
   # Check all backend functions have TTL
   for intent, config in INTENT_MAPPING.items():
       for func in config.get("suggested_functions", []):
           if func not in FUNCTION_TTL:
               errors.append(f"{intent} uses {func.value} but no TTL configured")
   
   # Validate category hints structure
   for category, hints in CATEGORY_QUESTION_HINTS.items():
       required_keys = ["budget_ranges", "preference_options", "common_use_cases"]
       for key in required_keys:
           if key not in hints:
               errors.append(f"Category {category} missing {key}")
   
   return errors


# Optional: Load from external source
def load_config_from_file(filepath: str) -> None:
   """Load configuration from JSON/YAML file. Useful for hot-reloading."""
   # Implementation depends on your preference
   # Could load from S3, Firestore, or local file
   pass