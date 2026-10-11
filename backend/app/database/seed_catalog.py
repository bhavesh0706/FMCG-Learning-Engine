"""
OPTIONAL starter / demo data for a NEW server. This is NOT where live data lives.

MySQL (fmcg_learning) is the single source of truth. Categories, products,
recipes, survey questions and question templates are created, edited and
removed in the app (Catalog page), which saves them straight to MySQL through
the API. Nothing in the application reads this file; you never need to edit it
to add a product.

This file is only used when you run the seed by hand, e.g. on an empty server:
    python -m app.database.seed_catalog              # same as: python -m app.database.seed
    python -m app.database.seed --dry-run            # preview, writes nothing
The seed only INSERTS what is missing and never changes or deletes existing data.

Question format: (text, type, options) or (text, type, options, report_role)
  type:        rating | single_choice | yes_no | text
  report_role: optional, for the client summary of the consolidated report:
               preference | price | score | improvement | purchase_intent | ease
Product keys:  name, sku, description, questions, and optionally recipe.
"""


# ----------------------------------------------------------------------------
# Default recipes (added only to products that have no recipe yet).
# To copy recipes edited in the app back into this file exactly:
#     python -m app.database.seed --export-recipes
# ----------------------------------------------------------------------------

AAM_JELLY_RECIPE = {
    "batch_size": "100 kg batch = approximately 2000 servings of 50 g",
    "ingredients": [
        {"name": "Mango pulp", "quantity": "35", "unit": "kg", "notes": "Main mango flavour"},
        {"name": "Sugar", "quantity": "45", "unit": "kg", "notes": "Sweetness"},
        {"name": "Water", "quantity": "12", "unit": "kg", "notes": "Mixing and consistency"},
        {"name": "Glucose syrup", "quantity": "5", "unit": "kg", "notes": "Texture and smoothness"},
        {"name": "Pectin", "quantity": "1.2", "unit": "kg", "notes": "Jelly setting"},
        {"name": "Citric acid", "quantity": "0.8", "unit": "kg", "notes": "Acidity and flavour balance"},
        {"name": "Permitted food flavour/colour", "quantity": "0.5", "unit": "kg", "notes": "Flavour and appearance"},
        {"name": "Preservative, if permitted", "quantity": "0.5", "unit": "kg", "notes": "Shelf-life support"},
    ],
    "steps": [
        {"title": "Prepare mango pulp", "temperature": "25–30 °C", "duration": "10 min",
         "details": "Inspect the mango pulp for quality, remove any unwanted particles, and prepare it for mixing."},
        {"title": "Mix sugar and pectin", "temperature": "40–50 °C", "duration": "10 min",
         "details": "Premix the sugar and pectin to help disperse the pectin evenly and reduce lump formation."},
        {"title": "Cook the mixture", "temperature": "85–90 °C", "duration": "20 min",
         "details": "Combine the mango pulp, water, sugar mixture and glucose syrup. Heat while stirring until the mixture is uniform."},
        {"title": "Adjust acidity and consistency", "temperature": "80–85 °C", "duration": "5 min",
         "details": "Add the citric acid according to the validated formulation. Check pH and soluble solids to achieve the required jelly consistency."},
        {"title": "Quality check", "temperature": "Product-specific", "duration": "10 min",
         "details": "Check colour, mango flavour, sweetness, consistency and texture. Add only approved flavouring, colouring or preservatives at validated levels."},
        {"title": "Fill and seal", "temperature": "Validated filling temperature", "duration": "15 min",
         "details": "Fill into clean, food-grade containers using a validated filling process. Seal securely, label the batch and check packaging integrity."},
    ],
    "notes": (
        "Store in a cool, dry place away from direct sunlight. Keep the container tightly closed after opening "
        "and use a clean, dry spoon. Monitor pH, soluble solids, texture, hygiene and packaging integrity. "
        "Determine shelf life through appropriate product testing and use only food additives permitted under "
        "applicable regulations."
    ),
}

DARK_CHOCOLATE_RECIPE = {
    "batch_size": "100 kg batch = approximately 2,000 servings of 50 g",
    "ingredients": [
        {"name": "Cocoa mass (cocoa liquor)", "quantity": "40", "unit": "kg", "notes": "Main chocolate flavour"},
        {"name": "Cocoa butter", "quantity": "22", "unit": "kg", "notes": "Smoothness and melting texture"},
        {"name": "Sugar", "quantity": "30", "unit": "kg", "notes": "Sweetness"},
        {"name": "Cocoa powder", "quantity": "7", "unit": "kg", "notes": "Rich cocoa flavour"},
        {"name": "Soy lecithin", "quantity": "0.5", "unit": "kg", "notes": "Emulsification and flow"},
        {"name": "Vanilla flavour", "quantity": "0.5", "unit": "kg", "notes": "Flavour balance"},
    ],
    "steps": [
        {"title": "Prepare ingredients", "temperature": "25–30 °C", "duration": "10 min",
         "details": "Weigh and inspect the cocoa mass, cocoa butter, sugar, cocoa powder and other ingredients."},
        {"title": "Melt cocoa butter", "temperature": "45–50 °C", "duration": "15 min",
         "details": "Melt the cocoa butter gently in suitable food-processing equipment."},
        {"title": "Mix ingredients", "temperature": "45–50 °C", "duration": "20 min",
         "details": "Add the cocoa mass, cocoa powder and sugar. Mix until the chocolate mass is uniform."},
        {"title": "Refine the chocolate", "temperature": "45–50 °C", "duration": "30 min",
         "details": "Refine the mixture to reduce particle size and achieve a smoother mouthfeel."},
        {"title": "Conch and flavour", "temperature": "45–60 °C", "duration": "2–6 hours",
         "details": "Continue mixing and refining according to the required flavour and texture. Add lecithin and vanilla according to the validated process."},
        {"title": "Temper the chocolate", "temperature": "Product-specific", "duration": "10 min",
         "details": "Use a validated dark-chocolate tempering curve to develop gloss, a clean snap and stable cocoa-butter crystals."},
        {"title": "Mould and cool", "temperature": "Controlled cooling", "duration": "20–30 min",
         "details": "Pour the tempered chocolate into moulds, remove air bubbles and cool under controlled conditions."},
        {"title": "Package", "temperature": "Cool, dry environment", "duration": "15 min",
         "details": "Demould, inspect the bars and seal them in suitable moisture- and odour-resistant packaging."},
    ],
    "notes": (
        "Store in a cool, dry place away from direct sunlight, strong odours and moisture. Maintain controlled "
        "storage temperatures to reduce blooming and preserve flavour and texture. Check cocoa content, particle "
        "size, tempering quality, weight and packaging integrity. This is a representative demo formulation, not a "
        "proprietary manufacturer's recipe; validate ingredients, processing conditions and food-safety "
        "requirements before commercial production."
    ),
}


KELLOGGS_CORN_FLAKES_RECIPE = {
    "batch_size": "100 kg batch = approximately 1000 servings of 100 g",
    "ingredients": [
        {"name": "Corn grits / maize", "quantity": "82", "unit": "kg", "notes": "Main cereal base"},
        {"name": "Sugar", "quantity": "8", "unit": "kg", "notes": "Sweetness"},
        {"name": "Malt flavouring", "quantity": "2", "unit": "kg", "notes": "Flavour"},
        {"name": "Salt", "quantity": "1", "unit": "kg", "notes": "Taste balance"},
        {"name": "Barley malt extract", "quantity": "2", "unit": "kg", "notes": "Flavour and browning"},
        {"name": "Vitamins & minerals premix", "quantity": "0.5", "unit": "kg", "notes": "Fortification"},
        {"name": "Vegetable oil", "quantity": "4.5", "unit": "kg", "notes": "Processing/coating"},
    ],
    "steps": [
        {"title": "Prepare and cook the corn grits with sugar, malt flavouring, salt, and water.",
         "temperature": "85 °C", "duration": "30 min",
         "details": "Mix thoroughly and cook until the corn mixture is evenly hydrated and softened."},
        {"title": "Roll the cooked corn mixture into thin flakes.",
         "temperature": "Room temperature", "duration": "10 min",
         "details": "Pass the conditioned corn through rollers to achieve a thin, uniform flake."},
        {"title": "Dry the flakes to reduce moisture.",
         "temperature": "110 °C", "duration": "20 min",
         "details": "Dry evenly until the flakes reach the desired moisture level and crispness."},
        {"title": "Toast the flakes until golden and crunchy.",
         "temperature": "180 °C", "duration": "8 min",
         "details": "Toast evenly without over-browning."},
        {"title": "Cool, fortify, and pack the Corn Flakes.",
         "temperature": "Room temperature", "duration": "15 min",
         "details": "Cool completely, apply the vitamin and mineral premix, then pack in moisture-resistant packaging."},
    ],
    "notes": (
        "Store in a cool, dry place. Keep the package tightly sealed after opening. Protect from moisture and "
        "direct sunlight to maintain freshness and crunchiness."
    ),
}


CATALOG = {
    (
        "Food & Confectionery",
        "Jellies, sweets, chocolates and snack foods.",
    ): [
        {
            "name": "Aam (Mango) Jelly",
            "sku": "FOOD-AAM-JELLY",
            "description": "Mango-flavoured jelly.",
            "recipe": AAM_JELLY_RECIPE,
            "questions": [
                (
                    "How often did you eat Aam Jelly in the period you used it?",
                    "single_choice",
                    ["Daily", "2-3 times a week", "Once a week", "Less often"],
                ),
                (
                    "How would you rate the mango taste?",
                    "rating",
                    None,
                ),
                (
                    "How is the sweetness?",
                    "single_choice",
                    ["Too sweet", "Just right", "Not sweet enough"],
                ),
                (
                    "How would you rate the texture?",
                    "rating",
                    None,
                ),
                (
                    "How easy was the pack to open and store?",
                    "rating",
                    None,
                ),
                (
                    "Is the price right for the quantity?",
                    "single_choice",
                    ["Too expensive", "Fair", "Good value"],
                ),
                (
                    "Would you buy Aam Jelly again?",
                    "yes_no",
                    None,
                ),
                (
                    "What would you change about it?",
                    "text",
                    None,
                ),
            ],
        },
        {
            "name": "Dark Chocolate",
            "sku": "FOOD-DARK-CHOCOLATE",
            "description": "Rich dark chocolate with a naturally bitter cocoa taste.",
            "recipe": DARK_CHOCOLATE_RECIPE,
            "questions": [
                (
                    "How often did you eat Dark Chocolate during the period you used it?",
                    "single_choice",
                    ["Daily", "2-3 times a week", "Once a week", "Less often"],
                ),
                (
                    "How would you rate the overall taste?",
                    "rating",
                    None,
                ),
                (
                    "How would you rate the bitterness?",
                    "single_choice",
                    ["Too bitter", "Just right", "Not bitter enough"],
                ),
                (
                    "How would you rate the sweetness?",
                    "single_choice",
                    ["Too sweet", "Just right", "Not sweet enough"],
                ),
                (
                    "How would you rate the texture and smoothness?",
                    "rating",
                    None,
                ),
                (
                    "Is the price right for the quantity?",
                    "single_choice",
                    ["Too expensive", "Fair", "Good value"],
                ),
                (
                    "Would you buy Dark Chocolate again?",
                    "yes_no",
                    None,
                ),
                (
                    "What would you change about it?",
                    "text",
                    None,
                ),
            ],
        },
    ],
    (
        "Breakfast Cereals",
        "Ready-to-eat breakfast cereals.",
    ): [
        {
            "name": "Kellogg's Corn Flakes",
            "sku": "FOOD-CORN-FLAKES",
            "description": "Toasted corn flakes breakfast cereal.",
            "questions": [
                (
                    "How often did you eat Kellogg's Corn Flakes during the period you used it?",
                    "single_choice",
                    ["Daily", "2-3 times a week", "Once a week", "Less often"],
                ),
                (
                    "How would you rate the corn flavour of Kellogg's Corn Flakes?",
                    "rating",
                    None,
                ),
                (
                    "How is the sweetness of Kellogg's Corn Flakes?",
                    "single_choice",
                    ["Too sweet", "Just right", "Not sweet enough"],
                ),
                (
                    "How would you rate the crunchiness of Kellogg's Corn Flakes?",
                    "rating",
                    None,
                ),
                (
                    "How well do Kellogg's Corn Flakes stay crunchy after adding milk?",
                    "rating",
                    None,
                ),
                (
                    "How easy is the Kellogg's Corn Flakes pack to open, pour and store?",
                    "rating",
                    None,
                ),
                (
                    "How do you rate the value of Kellogg's Corn Flakes for its quantity?",
                    "single_choice",
                    ["Too expensive", "Fair", "Good value"],
                ),
                (
                    "Would you buy Kellogg's Corn Flakes again?",
                    "yes_no",
                    None,
                ),
            ],
            "recipe": KELLOGGS_CORN_FLAKES_RECIPE,
        },
    ],
}


# ----------------------------------------------------------------------------
# Starter question templates (offered when adding a product in the app).
# Added only if no template with the same name exists. After that, templates are
# managed in the app: Catalog > Question templates / "Save as template".
# Questions use the same format as above; "category" is optional (suggested for it).
# ----------------------------------------------------------------------------

STARTER_QUESTION_TEMPLATES = [
    {
        "name": "Food product (standard)",
        "category": "Food & Confectionery",
        "description": "Usage, taste, sweetness, texture, pack, value, repurchase and suggestions.",
        "questions": [
            ("How often did you use the product during the period you used it?", "single_choice",
             ["Daily", "2-3 times a week", "Once a week", "Less often"]),
            ("How would you rate the taste?", "rating", None),
            ("How is the sweetness?", "single_choice", ["Too sweet", "Just right", "Not sweet enough"]),
            ("How would you rate the texture?", "rating", None),
            ("How easy is the pack to open and store?", "rating", None, "ease"),
            ("Is the price right for the quantity?", "single_choice", ["Too expensive", "Fair", "Good value"]),
            ("Would you buy it again?", "yes_no", None, "purchase_intent"),
            ("What would you change about it?", "text", None, "improvement"),
        ],
    },
]


def seed(argv=None):
    """Write CATALOG with the safe, insert-only engine (app/database/seed.py)."""
    from app.database.seed import main

    return main(argv)


if __name__ == "__main__":
    import sys

    sys.exit(seed(sys.argv[1:]))