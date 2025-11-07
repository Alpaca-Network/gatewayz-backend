"""Test module to verify Claude on-failure workflow."""


def hello_world():
    """Simple function with extra spaces."""
    x = 1  # noqa: F841 - unused variable (intentional for testing)
    y   =   2  # Multiple spaces - will fail linting
    print("Hello World")
    return y


def another_function( ):
    """Function with linting issues."""
    unused_var = "this is unused"  # noqa: F841
    return "done"
