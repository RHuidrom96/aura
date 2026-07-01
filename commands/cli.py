def register_cli(app):
    """Register `flask` CLI commands. Imports seed_data lazily inside each
    command (rather than at module import time) so a missing/broken seed_data
    module doesn't prevent the app itself from starting -- these are optional
    dev-only commands."""

    @app.cli.command("seed-demo")
    def seed_demo_command():
        """Load the Northeast India language demo campaigns, annotators, and ratings."""
        import seed_data
        from models import db, Campaign, Annotator, Rating

        with app.app_context():
            summary = seed_data.seed_all(db, Campaign, Annotator, Rating)
        print("Demo campaigns created: %d" % len(summary["campaigns"]))
        for name in summary["campaigns"]:
            print("  -", name)
        print("Demo annotators created: %d" % summary["annotators"])
        print("Synthetic ratings created: %d" % summary["ratings"])

    @app.cli.command("unload-demo")
    def unload_demo_command():
        """Remove the demo campaigns, annotators, and synthetic ratings."""
        import seed_data
        from models import db, Campaign, Annotator, Rating, AssistantLog

        with app.app_context():
            summary = seed_data.unload_demo(db, Campaign, Annotator, Rating, AssistantLog)
        print("Removed %(campaigns)d campaign(s), %(annotators)d annotator(s), "
              "%(ratings)d rating(s)." % summary)
