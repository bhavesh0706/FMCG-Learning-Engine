"""
The Super Admin's login (the person a client company appoints to manage its products,
recipes and rounds). Same commands as app.tools.organization, which stays available:

    python -m app.tools.super_admin create --name "Acme Foods" --email insights@acmefoods.com
    python -m app.tools.super_admin reset-password --email insights@acmefoods.com
    python -m app.tools.super_admin list

Internally the role is still "client" in the database and the API.
"""

from app.tools.organization import main

if __name__ == "__main__":
    main()