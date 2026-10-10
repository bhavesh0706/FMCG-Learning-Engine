"""
Sample catalog: Aam (Mango) Jelly and Dark Chocolate, with survey questions.

Run from the backend folder:
    python -m app.database.seed_catalog

Safe to run again: existing categories/products/questions are left as they are.
"""

from app.database.database import SessionLocal
from app.database.migrations import run_migrations
from app.database.models import Category, Product, SurveyQuestion


CATALOG = {
    (
        "Food & Confectionery",
        "Jellies, sweets, chocolates and snack foods.",
    ): [
        {
            "name": "Aam (Mango) Jelly",
            "sku": "FOOD-AAM-JELLY",
            "description": "Mango-flavoured jelly.",
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
}


def seed():
    run_migrations()

    db = SessionLocal()

    added = {
        "categories": 0,
        "products": 0,
        "questions": 0,
    }

    try:
        for (cat_name, cat_desc), products in CATALOG.items():

            # Find existing category
            category = (
                db.query(Category)
                .filter(Category.name == cat_name)
                .first()
            )

            # Create category if it does not exist
            if not category:
                category = Category(
                    name=cat_name,
                    description=cat_desc,
                )

                db.add(category)
                db.flush()

                added["categories"] += 1

            for spec in products:

                # Find existing product in this category
                product = (
                    db.query(Product)
                    .filter(
                        Product.category_id == category.id,
                        Product.name == spec["name"],
                    )
                    .first()
                )

                # Create product if it does not exist
                if not product:
                    product = Product(
                        category_id=category.id,
                        name=spec["name"],
                        sku=spec.get("sku"),
                        description=spec.get("description"),
                        min_responses=10,
                    )

                    db.add(product)
                    db.flush()

                    added["products"] += 1

                # Do not duplicate questions
                has_questions = (
                    db.query(SurveyQuestion)
                    .filter(
                        SurveyQuestion.product_id == product.id
                    )
                    .first()
                )

                if has_questions:
                    continue

                # Add product questions
                for position, (text, qtype, options) in enumerate(
                    spec["questions"],
                    start=1,
                ):
                    db.add(
                        SurveyQuestion(
                            product_id=product.id,
                            position=position,
                            text=text,
                            question_type=qtype,
                            options=options,
                            required=1,
                        )
                    )

                    added["questions"] += 1

        db.commit()

        print(
            f"Catalog ready. "
            f"Added {added['categories']} categories, "
            f"{added['products']} products, "
            f"{added['questions']} questions."
        )

    finally:
        db.close()


if __name__ == "__main__":
    seed()