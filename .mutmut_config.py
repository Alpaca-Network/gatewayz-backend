"""
Mutation Testing Configuration for mutmut

Mutation testing verifies test quality by introducing bugs (mutations)
and checking if tests catch them.

Install: pip install mutmut
Run: mutmut run
View results: mutmut results
HTML report: mutmut html

Documentation: https://mutmut.readthedocs.io/
"""

def pre_mutation(context):
    """
    Called before each mutation.
    Can be used to skip certain mutations.
    """
    # Skip mutations in test files
    if 'tests/' in context.filename:
        context.skip = True

    # Skip migrations
    if 'migrations/' in context.filename:
        context.skip = True

    # Skip documentation
    if context.filename.endswith('.md'):
        context.skip = True

    # Skip configuration files
    if context.filename in ['.mutmut_config.py', 'pyproject.toml', 'pytest.ini']:
        context.skip = True


def post_mutation(context):
    """
    Called after each mutation.
    Can be used for custom reporting.
    """
    pass
