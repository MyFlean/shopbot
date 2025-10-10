# shopping_bot/macro_optimizer.py
"""
Macro Optimizer: Category-aware nutritional intelligence
────────────────────────────────────────────────────────
Merges user-specified macro constraints with category-specific defaults

This module provides intelligent macro filtering and ranking based on:
1. User-specified nutritional constraints (explicit)
2. Category-specific nutritional profiles (implicit)
3. Health condition inference (smart defaults)

Usage:
    optimizer = get_macro_optimizer()
    merged = optimizer.merge_constraints(
        user_constraints=[{"nutrient_name": "protein g", "operator": "gt", "value": 20}],
        category_path="f_and_b/food/light_bites/chips_and_crisps"
    )
    # Returns: {hard_filters: [...], soft_boosts: [...], display_priority: [...]}
"""

import json
import os
from typing import Dict, List, Any, Optional
from pathlib import Path
import logging

log = logging.getLogger(__name__)


class MacroOptimizer:
    """Intelligent macro filtering and ranking based on category profiles"""
    
    def __init__(self):
        """Initialize optimizer and load category profiles"""
        self.profiles = self._load_category_profiles()
        log.info(f"MacroOptimizer initialized with {len(self.profiles)} category profiles")
    
    def _load_category_profiles(self) -> Dict[str, Any]:
        """Load category macro profiles from JSON"""
        try:
            profile_path = Path(__file__).parent / "taxonomies" / "category_macro_profiles.json"
            with open(profile_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # Remove metadata key from profiles
                if "_metadata" in data:
                    del data["_metadata"]
                return data
        except FileNotFoundError:
            log.error("category_macro_profiles.json not found, using default profile only")
            return self._get_default_profile()
        except json.JSONDecodeError as e:
            log.error(f"Failed to parse category_macro_profiles.json: {e}")
            return self._get_default_profile()
        except Exception as e:
            log.error(f"Failed to load category_macro_profiles.json: {e}")
            return self._get_default_profile()
    
    def _get_default_profile(self) -> Dict[str, Any]:
        """Return minimal default profile structure"""
        return {
            "_default": {
                "optimize": {
                    "maximize": [
                        {"field": "protein g", "weight": 1.1, "ideal_min": 5.0},
                        {"field": "fiber g", "weight": 1.1, "ideal_min": 3.0}
                    ],
                    "minimize": [
                        {"field": "sodium mg", "weight": 0.85, "ideal_max": 400},
                        {"field": "saturated fat g", "weight": 0.85, "ideal_max": 5.0},
                        {"field": "trans fat g", "weight": 0.8, "ideal_max": 0.2},
                        {"field": "added sugar g", "weight": 0.85, "ideal_max": 10.0}
                    ]
                },
                "display_priority": ["sodium mg", "added sugar g", "saturated fat g", "protein g"],
                "health_context": "General nutritional optimization for uncategorized products"
            }
        }
    
    def get_category_profile(self, category_path: str) -> Dict[str, Any]:
        """
        Extract L3 category from full path and return its profile
        
        Args:
            category_path: Full path like "f_and_b/food/light_bites/chips_and_crisps"
        
        Returns:
            Category profile dict with optimize rules
        
        Examples:
            >>> optimizer.get_category_profile("f_and_b/food/light_bites/chips_and_crisps")
            {"optimize": {"maximize": [...], "minimize": [...]}, "display_priority": [...]}
        """
        if not category_path:
            return self.profiles.get("_default", self._get_default_profile()["_default"])
        
        # Extract L2 and L3 from path
        parts = [p for p in str(category_path).split('/') if p]
        
        # Path format: f_and_b/food/{L2}/{L3} or f_and_b/beverages/{L2}/{L3}
        if len(parts) >= 4:
            l2 = parts[2]  # e.g., "light_bites"
            l3 = parts[3]  # e.g., "chips_and_crisps"
            
            # Check L2 → L3 nested structure
            if l2 in self.profiles and isinstance(self.profiles[l2], dict):
                if l3 in self.profiles[l2]:
                    profile = self.profiles[l2][l3]
                    if isinstance(profile, dict) and "optimize" in profile:
                        log.debug(f"Found profile for {l2}/{l3}")
                        return profile
        
        # Fallback to L2 level if L3 not found
        if len(parts) >= 3:
            l2 = parts[2]
            if l2 in self.profiles and isinstance(self.profiles[l2], dict):
                # Return first valid L3 profile in that L2 as representative
                for l3_key, l3_profile in self.profiles[l2].items():
                    if isinstance(l3_profile, dict) and "optimize" in l3_profile:
                        log.debug(f"Using representative profile from {l2}/{l3_key} for L2={l2}")
                        return l3_profile
        
        # Final fallback to _default
        log.debug(f"No specific profile for {category_path}, using _default")
        return self.profiles.get("_default", self._get_default_profile()["_default"])
    
    def merge_constraints(
        self,
        user_constraints: Optional[List[Dict[str, Any]]] = None,
        category_path: Optional[str] = None,
        apply_category_defaults: bool = True
    ) -> Dict[str, Any]:
        """
        Merge user-specified constraints with category defaults
        
        Args:
            user_constraints: List of macro_filters from LLM, format:
                [{"nutrient_name": "protein g", "operator": "gt", "value": 20, "priority": "hard"}]
            category_path: Product category path (e.g., "f_and_b/food/light_bites/chips_and_crisps")
            apply_category_defaults: If False, skip category defaults (use only user constraints)
        
        Returns:
            {
                "hard_filters": [
                    {"nutrient": "protein g", "operator": "gt", "value": 20, "source": "user"}
                ],
                "soft_boosts": [
                    {"nutrient": "sodium mg", "operator": "lte", "value": 300, "weight": 0.8, "source": "category_default"}
                ],
                "display_priority": ["protein g", "sodium mg", "saturated fat g"],
                "has_constraints": True  # True if any user constraints exist
            }
        
        Priority Rules:
            1. User-specified constraints always become hard filters (with exists check)
            2. Category defaults only applied if apply_category_defaults=True
            3. If no user constraints, category defaults are NOT applied (use percentile ranking instead)
        """
        hard_filters = []
        soft_boosts = []
        user_nutrients = set()  # Track which nutrients user explicitly mentioned
        has_user_constraints = False
        
        # 1. Process user-specified constraints
        if user_constraints:
            for constraint in user_constraints:
                if not constraint or not isinstance(constraint, dict):
                    continue
                
                nutrient = constraint.get("nutrient_name")
                operator = constraint.get("operator")
                value = constraint.get("value")
                priority = constraint.get("priority", "hard")  # Default to hard if not specified
                
                # Validation
                if not nutrient:
                    log.warning(f"Skipping constraint with missing nutrient_name: {constraint}")
                    continue
                if not operator:
                    log.warning(f"Skipping constraint with missing operator: {constraint}")
                    continue
                if value is None:
                    log.warning(f"Skipping constraint with missing value: {constraint}")
                    continue
                
                # Normalize nutrient name if needed
                normalized_nutrient = self.normalize_nutrient_name(nutrient)
                if normalized_nutrient:
                    nutrient = normalized_nutrient
                
                # Validate nutrient name
                if not self.validate_nutrient_name(nutrient):
                    log.warning(f"Unrecognized nutrient '{nutrient}', may not exist in ES mapping")
                
                # Track that user specified this nutrient
                user_nutrients.add(nutrient)
                has_user_constraints = True
                
                # Add to appropriate list based on priority
                if priority == "hard":
                    hard_filters.append({
                        "nutrient": nutrient,
                        "operator": operator,
                        "value": value,
                        "source": "user"
                    })
                    log.debug(f"Added user hard filter: {nutrient} {operator} {value}")
                else:  # soft
                    # Determine weight based on operator (maximize vs minimize)
                    default_weight = 1.2 if operator in ["gte", "gt"] else 0.85
                    soft_boosts.append({
                        "nutrient": nutrient,
                        "operator": operator,
                        "value": value,
                        "weight": default_weight,
                        "source": "user_soft"
                    })
                    log.debug(f"Added user soft boost: {nutrient} {operator} {value} weight={default_weight}")
        
        # 2. Add category-specific defaults (only if user has constraints AND apply_category_defaults=True)
        display_priority = []
        
        if category_path and apply_category_defaults and has_user_constraints:
            profile = self.get_category_profile(category_path)
            optimize = profile.get("optimize", {})
            
            # Process maximize targets (soft boosts for higher values)
            for item in optimize.get("maximize", []):
                if not isinstance(item, dict):
                    continue
                    
                nutrient = item.get("field")
                if not nutrient:
                    continue
                
                # Skip if user already specified this nutrient
                if nutrient in user_nutrients:
                    log.debug(f"Skipping category default for {nutrient} (user override)")
                    continue
                
                ideal_min = item.get("ideal_min", 0)
                weight = item.get("weight", 1.2)
                
                soft_boosts.append({
                    "nutrient": nutrient,
                    "operator": "gte",
                    "value": ideal_min,
                    "weight": weight,
                    "source": "category_default"
                })
                log.debug(f"Added category soft boost (maximize): {nutrient} >= {ideal_min} weight={weight}")
            
            # Process minimize targets (soft penalties for lower values)
            for item in optimize.get("minimize", []):
                if not isinstance(item, dict):
                    continue
                    
                nutrient = item.get("field")
                if not nutrient:
                    continue
                
                # Skip if user already specified this nutrient
                if nutrient in user_nutrients:
                    log.debug(f"Skipping category default for {nutrient} (user override)")
                    continue
                
                # Check if there's an ideal_max (range optimization)
                ideal_max = item.get("ideal_max")
                if ideal_max is not None:
                    weight = item.get("weight", 0.85)
                    
                    soft_boosts.append({
                        "nutrient": nutrient,
                        "operator": "lte",
                        "value": ideal_max,
                        "weight": weight,
                        "source": "category_default"
                    })
                    log.debug(f"Added category soft boost (minimize): {nutrient} <= {ideal_max} weight={weight}")
            
            # Get display priority from profile
            display_priority = profile.get("display_priority", [])
        elif has_user_constraints:
            # User has constraints but no category path - use user-specified nutrients as display priority
            display_priority = list(user_nutrients)
        
        result = {
            "hard_filters": hard_filters,
            "soft_boosts": soft_boosts,
            "display_priority": display_priority,
            "has_constraints": has_user_constraints
        }
        
        log.info(f"Merged constraints: {len(hard_filters)} hard filters, {len(soft_boosts)} soft boosts, has_user_constraints={has_user_constraints}")
        return result
    
    def validate_nutrient_name(self, nutrient: str) -> bool:
        """
        Validate that nutrient field exists in nutri_breakdown_updated
        
        Args:
            nutrient: Field name like "protein g", "sodium mg", etc.
        
        Returns:
            True if nutrient is recognized, False otherwise
        
        Common fields:
        - Macros: protein g, carbohydrate g, total fat g, fiber g, total sugar g, added sugar g
        - Fats: saturated fat g, trans fat g, unsaturated fat g, mufa g, pufa g
        - Minerals: sodium mg, calcium mg, iron mg, potassium mg, zinc mg, magnesium mg
        - Vitamins: vitamin a mcg, vitamin b12 mcg, vitamin c mg, vitamin d mcg, vitamin e mg
        - Special: caffeine mg, cholesterol mg, energy kcal
        """
        valid_nutrients = {
            # Macros
            "protein g", "carbohydrate g", "carbs g", "total fat g", "fat g",
            "fiber g", "total sugar g", "added sugar g", "natural sugar g",
            "energy kcal", "calories kcal",
            
            # Fats
            "saturated fat g", "trans fat g", "unsaturated fat g", "mufa g", "pufa g",
            
            # Minerals (mg)
            "sodium mg", "calcium mg", "iron mg", "potassium mg", "zinc mg",
            "magnesium mg", "phosphorous mg", "chloride mg", "selenium mg",
            "copper mg", "manganese mg",
            
            # Vitamins
            "vitamin a mcg", "vitamin b12 mcg", "vitamin c mg", "vitamin d mcg",
            "vitamin e mg", "vitamin k mcg", "folate mcg", "niacin mg",
            "riboflavin mg", "thiamine mg",
            
            # Special compounds
            "caffeine mg", "cholesterol mg", "taurine mg", "glucuronolactone mg",
            "l-theanine mg", "melatonin mg", "omega 3 dha mg", "omega 3 ala mg",
        }
        
        return nutrient.lower() in {n.lower() for n in valid_nutrients}
    
    def normalize_nutrient_name(self, user_input: str) -> Optional[str]:
        """
        Normalize user-friendly nutrient names to ES field names
        
        Args:
            user_input: User-friendly name like "protein", "sodium", "calories"
        
        Returns:
            Normalized ES field name like "protein g", "sodium mg", "energy kcal"
            Returns None if no mapping found (use original)
        
        Examples:
            >>> optimizer.normalize_nutrient_name("protein")
            "protein g"
            >>> optimizer.normalize_nutrient_name("sodium")
            "sodium mg"
            >>> optimizer.normalize_nutrient_name("calories")
            "energy kcal"
        """
        mapping = {
            # Macros
            "protein": "protein g",
            "carbs": "carbohydrate g",
            "carbohydrate": "carbohydrate g",
            "carbohydrates": "carbohydrate g",
            "fat": "total fat g",
            "fiber": "fiber g",
            "fibre": "fiber g",
            "sugar": "total sugar g",
            "sugars": "total sugar g",
            "added sugar": "added sugar g",
            "added sugars": "added sugar g",
            "calories": "energy kcal",
            "energy": "energy kcal",
            
            # Fats
            "saturated fat": "saturated fat g",
            "sat fat": "saturated fat g",
            "trans fat": "trans fat g",
            "unsaturated fat": "unsaturated fat g",
            
            # Minerals
            "sodium": "sodium mg",
            "salt": "sodium mg",
            "calcium": "calcium mg",
            "iron": "iron mg",
            "potassium": "potassium mg",
            "zinc": "zinc mg",
            "magnesium": "magnesium mg",
            
            # Vitamins
            "vitamin a": "vitamin a mcg",
            "vitamin b12": "vitamin b12 mcg",
            "vitamin c": "vitamin c mg",
            "vitamin d": "vitamin d mcg",
            "vitamin e": "vitamin e mg",
            "vitamin k": "vitamin k mcg",
            
            # Special
            "caffeine": "caffeine mg",
            "cholesterol": "cholesterol mg",
        }
        
        user_lower = user_input.lower().strip()
        normalized = mapping.get(user_lower)
        
        if normalized:
            log.debug(f"Normalized '{user_input}' → '{normalized}'")
        
        return normalized


# Singleton instance
_optimizer: Optional[MacroOptimizer] = None


def get_macro_optimizer() -> MacroOptimizer:
    """
    Get or create macro optimizer singleton
    
    Returns:
        MacroOptimizer instance
    
    Usage:
        optimizer = get_macro_optimizer()
        result = optimizer.merge_constraints(...)
    """
    global _optimizer
    if _optimizer is None:
        _optimizer = MacroOptimizer()
    return _optimizer

