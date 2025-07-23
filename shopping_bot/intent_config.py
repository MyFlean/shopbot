"""
Centralized configuration for intent mappings, slot requirements, and questions.

This module serves as the single source of truth for:
- Intent classification mappings
- Required slots and functions per intent
- Question generation hints and fallbacks
- Category-specific hints
- Function TTLs and other configuration
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
        "suggested_slots": [UserSlot.USER_PREFERENCES, UserSlot.USER_BUDGET],
        "suggested_functions": [BackendFunction.FETCH_PRODUCT_INVENTORY],
    },
    "Recommendation": {
        "query_intent": QueryIntent.RECOMMENDATION,
        "suggested_slots": [UserSlot.USER_PREFERENCES, UserSlot.USER_BUDGET, UserSlot.USE_CASE],
        "suggested_functions": [
            BackendFunction.FETCH_PRODUCT_INVENTORY,
            BackendFunction.FETCH_PURCHASE_HISTORY,
            BackendFunction.FETCH_USER_PROFILE,
        ],
    },
    
    # B1: Consideration - Catalogue
    "Specific_Product_Search": {
        "query_intent": QueryIntent.PRODUCT_SEARCH,
        "suggested_slots": [UserSlot.PRODUCT_NAME],
        "suggested_functions": [
            BackendFunction.FETCH_PRODUCT_INVENTORY,
            BackendFunction.FETCH_PRODUCT_DETAILS,
        ],
    },
    "Product_Comparison": {
        "query_intent": QueryIntent.PRODUCT_COMPARISON,
        "suggested_slots": [UserSlot.PRODUCTS_TO_COMPARE, UserSlot.USER_PREFERENCES],
        "suggested_functions": [
            BackendFunction.FETCH_PRODUCT_DETAILS,
            BackendFunction.FETCH_PRODUCT_REVIEWS,
        ],
    },
    "Price_Inquiry": {
        "query_intent": QueryIntent.PRICE_INQUIRY,
        "suggested_slots": [UserSlot.PRODUCT_NAME],
        "suggested_functions": [BackendFunction.FETCH_PRODUCT_DETAILS],
    },
    
    # B2: Consideration - Logistics
    "Availability_Delivery_Inquiry": {
        "query_intent": QueryIntent.PRICE_INQUIRY,  # Reusing existing enum
        "suggested_slots": [UserSlot.PRODUCT_NAME, UserSlot.DELIVERY_ADDRESS],
        "suggested_functions": [
            BackendFunction.FETCH_PRODUCT_INVENTORY,
            BackendFunction.FETCH_PRODUCT_DETAILS,
        ],
    },
    
    # C1: Transaction
    "Purchase_Checkout": {
        "query_intent": QueryIntent.PURCHASE,
        "suggested_slots": [
            UserSlot.PRODUCT_NAME,
            UserSlot.QUANTITY,
            UserSlot.DELIVERY_ADDRESS,
        ],
        "suggested_functions": [
            BackendFunction.FETCH_PRODUCT_INVENTORY,
            BackendFunction.FETCH_PRODUCT_DETAILS,
            BackendFunction.FETCH_USER_PROFILE,
        ],
    },
    "Order_Modification": {
        "query_intent": QueryIntent.PURCHASE,
        "suggested_slots": [UserSlot.ORDER_ID, UserSlot.MODIFICATION_TYPE],
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
        "suggested_slots": [UserSlot.ORDER_ID, UserSlot.RETURN_REASON],
        "suggested_functions": [
            BackendFunction.FETCH_PURCHASE_HISTORY,
            BackendFunction.FETCH_PRODUCT_DETAILS,
        ],
    },
    
    # D2: Post-Purchase - Engagement
    "Feedback_Review_Submission": {
        "query_intent": QueryIntent.GENERAL_HELP,
        "suggested_slots": [UserSlot.ORDER_ID],
        "suggested_functions": [BackendFunction.FETCH_PURCHASE_HISTORY],
    },
    "Subscription_Reorder": {
        "query_intent": QueryIntent.PURCHASE,
        "suggested_slots": [UserSlot.PRODUCT_NAME, UserSlot.QUANTITY],
        "suggested_functions": [
            BackendFunction.FETCH_PURCHASE_HISTORY,
            BackendFunction.FETCH_PRODUCT_DETAILS,
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
   UserSlot.PRODUCT_NAME: "product_name",
   UserSlot.USE_CASE: "use_case",
   UserSlot.PRODUCTS_TO_COMPARE: "products_to_compare",
   UserSlot.QUANTITY: "quantity",
   UserSlot.ORDER_ID: "order_id",
   UserSlot.MODIFICATION_TYPE: "modification_type",
   UserSlot.RETURN_REASON: "return_reason",
}


# ─────────────────────────────────────────────────────────────
# Question Generation Hints and Fallbacks
# ─────────────────────────────────────────────────────────────
SLOT_QUESTIONS: Dict[UserSlot, Dict[str, Any]] = {
   UserSlot.USER_BUDGET: {
       "generation_hints": {
           "type": "budget_input",
           "should_include_options": True,
           "option_count": 4,
           "consider_factors": ["product_category", "price_range", "market_segment"],
           "adaptive": True,
       },
       "fallback": {
           "message": "What's your budget range?",
           "type": "budget_input",
           "options": ["Under ₹10k", "₹10k-₹30k", "₹30k-₹50k", "Above ₹50k"],
       }
   },
   UserSlot.USER_PREFERENCES: {
       "generation_hints": {
           "type": "preferences_input",
           "should_include_options": True,
           "option_count": 5,
           "consider_factors": ["product_type", "use_case", "category_specific_features"],
           "allow_multiple": True,
       },
       "fallback": {
           "message": "What features matter most to you?",
           "type": "preferences_input",
           "hints": ["Consider size, brand, quality, features, etc."],
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
           "type": "address_input",
           "placeholder": "Enter your full address with PIN code",
       }
   },
   UserSlot.PRODUCT_NAME: {
       "generation_hints": {
           "type": "product_input",
           "suggest_from_inventory": True,
           "include_autocomplete": True,
       },
       "fallback": {
           "message": "Which product are you looking for?",
           "type": "product_input",
           "placeholder": "Enter product name or model",
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
           "type": "text_input",
           "hints": ["Personal use", "Gift", "Business", "Other"],
       }
   },
   UserSlot.PRODUCTS_TO_COMPARE: {
       "generation_hints": {
           "type": "product_list_input",
           "max_items": 4,
           "suggest_popular_comparisons": True,
       },
       "fallback": {
           "message": "Which products would you like to compare?",
           "type": "product_list_input",
           "placeholder": "Enter product names separated by commas",
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
           "type": "quantity_input",
           "min": 1,
           "max": 100,
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
           "type": "order_id_input",
           "placeholder": "e.g., ORD-12345-67890",
       }
   },
   UserSlot.MODIFICATION_TYPE: {
       "generation_hints": {
           "type": "modification_input",
           "show_available_modifications": True,
           "context_aware": True,
       },
       "fallback": {
           "message": "What would you like to modify?",
           "type": "modification_input",
           "options": [
               "Cancel order",
               "Change delivery address",
               "Change quantity",
               "Update payment method",
           ],
       }
   },
   UserSlot.RETURN_REASON: {
       "generation_hints": {
           "type": "return_reason_input",
           "include_policy_info": True,
           "allow_other_option": True,
       },
       "fallback": {
           "message": "Why would you like to return this item?",
           "type": "return_reason_input",
           "options": [
               "Item defective or damaged",
               "Wrong item received",
               "Not as described",
               "Changed my mind",
               "Better price available",
               "Other",
           ],
       }
   },
}


# ─────────────────────────────────────────────────────────────
# Category-Specific Question Hints
# ─────────────────────────────────────────────────────────────
CATEGORY_QUESTION_HINTS = {
   "electronics": {
       "budget_ranges": ["Under ₹20k", "₹20k-₹50k", "₹50k-₹1L", "Above ₹1L"],
       "preference_options": ["Performance", "Battery life", "Camera quality", "Display", "Brand", "Storage"],
       "common_use_cases": ["Daily use", "Gaming", "Professional work", "Photography", "Entertainment"],
   },
   "fmcg": {
       "budget_ranges": ["Under ₹100", "₹100-₹500", "₹500-₹1000", "Above ₹1000"],
       "preference_options": ["Brand", "Organic", "Quantity/Size", "Fragrance", "Ingredients", "Eco-friendly"],
       "common_use_cases": ["Personal use", "Family pack", "Travel size", "Bulk purchase"],
   },
   "fashion": {
       "budget_ranges": ["Under ₹1000", "₹1000-₹3000", "₹3000-₹5000", "Above ₹5000"],
       "preference_options": ["Size", "Color", "Material", "Brand", "Style", "Fit"],
       "common_use_cases": ["Casual wear", "Formal/Office", "Party/Special occasion", "Sports/Active"],
   },
   "home_appliances": {
       "budget_ranges": ["Under ₹5k", "₹5k-₹15k", "₹15k-₹30k", "Above ₹30k"],
       "preference_options": ["Energy efficiency", "Brand", "Capacity", "Features", "Warranty", "Size"],
       "common_use_cases": ["Small family", "Large family", "Commercial use", "Compact spaces"],
   },
   "general": {
       "budget_ranges": ["Low", "Medium", "High", "Premium"],
       "preference_options": ["Quality", "Brand", "Features", "Value for money", "Durability"],
       "common_use_cases": ["Personal use", "Gift", "Business", "Other"],
   }
}


# ─────────────────────────────────────────────────────────────
# Function TTL Configuration
# ─────────────────────────────────────────────────────────────
FUNCTION_TTL: Dict[BackendFunction, timedelta] = {
   BackendFunction.FETCH_PRODUCT_INVENTORY: timedelta(minutes=5),
   BackendFunction.FETCH_PRODUCT_DETAILS: timedelta(minutes=30),
   BackendFunction.FETCH_PRODUCT_REVIEWS: timedelta(hours=1),
   BackendFunction.FETCH_SIMILAR_PRODUCTS: timedelta(hours=1),
   BackendFunction.FETCH_USER_PROFILE: timedelta(minutes=15),
   BackendFunction.FETCH_PURCHASE_HISTORY: timedelta(minutes=10),
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
   
   # Check all suggested slots have questions (now checking for either hints or fallback)
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