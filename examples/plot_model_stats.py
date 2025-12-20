#!/usr/bin/env python3
"""
Example: Plotting chat completion request data for visual analysis.

This script demonstrates how to use the individual request records to create
graphs and visualizations for analyzing model performance over time.

Requirements:
    pip install matplotlib pandas

Usage:
    python examples/plot_model_stats.py --query "gpt 4" --provider openrouter
    python examples/plot_model_stats.py --query "claude" --output charts/
"""

import argparse
import json
import sys
from pathlib import Path
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.models_requests_search import search_chat_requests

try:
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    import pandas as pd
    PLOTTING_AVAILABLE = True
except ImportError:
    PLOTTING_AVAILABLE = False
    print("Warning: matplotlib and/or pandas not installed. Install with: pip install matplotlib pandas")


def plot_tokens_over_time(model_name, requests_data, output_dir=None):
    """Plot input/output tokens over time."""
    if not requests_data:
        print(f"No data to plot for {model_name}")
        return

    # Convert to DataFrame
    df = pd.DataFrame(requests_data)
    df['created_at'] = pd.to_datetime(df['created_at'])
    df = df.sort_values('created_at')

    # Create figure
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8))
    fig.suptitle(f'{model_name} - Token Usage Over Time', fontsize=16, fontweight='bold')

    # Plot 1: Token counts
    ax1.plot(df['created_at'], df['input_tokens'], label='Input Tokens', marker='o', alpha=0.7)
    ax1.plot(df['created_at'], df['output_tokens'], label='Output Tokens', marker='s', alpha=0.7)
    ax1.plot(df['created_at'], df['total_tokens'], label='Total Tokens', marker='^', alpha=0.7, linestyle='--')
    ax1.set_ylabel('Token Count', fontsize=12)
    ax1.set_title('Token Usage per Request')
    ax1.legend()
    ax1.grid(True, alpha=0.3)
    ax1.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d %H:%M'))
    plt.setp(ax1.xaxis.get_majorticklabels(), rotation=45, ha='right')

    # Plot 2: Cumulative tokens
    df['cumulative_tokens'] = df['total_tokens'].cumsum()
    ax2.fill_between(df['created_at'], df['cumulative_tokens'], alpha=0.3)
    ax2.plot(df['created_at'], df['cumulative_tokens'], marker='o', linewidth=2)
    ax2.set_xlabel('Timestamp', fontsize=12)
    ax2.set_ylabel('Cumulative Tokens', fontsize=12)
    ax2.set_title('Cumulative Token Usage')
    ax2.grid(True, alpha=0.3)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d %H:%M'))
    plt.setp(ax2.xaxis.get_majorticklabels(), rotation=45, ha='right')

    plt.tight_layout()

    # Save or show
    if output_dir:
        output_path = Path(output_dir) / f"{model_name.replace('/', '_')}_tokens.png"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        print(f"Saved: {output_path}")
    else:
        plt.show()

    plt.close()


def plot_processing_time(model_name, requests_data, output_dir=None):
    """Plot processing time trends."""
    if not requests_data:
        print(f"No data to plot for {model_name}")
        return

    df = pd.DataFrame(requests_data)
    df['created_at'] = pd.to_datetime(df['created_at'])
    df = df.sort_values('created_at')

    # Create figure
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8))
    fig.suptitle(f'{model_name} - Processing Time Analysis', fontsize=16, fontweight='bold')

    # Plot 1: Processing time scatter with rolling average
    ax1.scatter(df['created_at'], df['processing_time_ms'], alpha=0.5, s=30, label='Individual Requests')

    # Add rolling average
    if len(df) >= 5:
        rolling_avg = df.set_index('created_at')['processing_time_ms'].rolling(window=5, min_periods=1).mean()
        ax1.plot(rolling_avg.index, rolling_avg.values, color='red', linewidth=2,
                label='5-Request Moving Average', alpha=0.8)

    ax1.set_ylabel('Processing Time (ms)', fontsize=12)
    ax1.set_title('Processing Time per Request')
    ax1.legend()
    ax1.grid(True, alpha=0.3)
    ax1.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d %H:%M'))
    plt.setp(ax1.xaxis.get_majorticklabels(), rotation=45, ha='right')

    # Plot 2: Distribution histogram
    ax2.hist(df['processing_time_ms'], bins=30, alpha=0.7, edgecolor='black')
    ax2.axvline(df['processing_time_ms'].mean(), color='red', linestyle='--',
               linewidth=2, label=f"Mean: {df['processing_time_ms'].mean():.0f}ms")
    ax2.axvline(df['processing_time_ms'].median(), color='green', linestyle='--',
               linewidth=2, label=f"Median: {df['processing_time_ms'].median():.0f}ms")
    ax2.set_xlabel('Processing Time (ms)', fontsize=12)
    ax2.set_ylabel('Frequency', fontsize=12)
    ax2.set_title('Processing Time Distribution')
    ax2.legend()
    ax2.grid(True, alpha=0.3, axis='y')

    plt.tight_layout()

    if output_dir:
        output_path = Path(output_dir) / f"{model_name.replace('/', '_')}_processing_time.png"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        print(f"Saved: {output_path}")
    else:
        plt.show()

    plt.close()


def plot_success_rate(model_name, requests_data, output_dir=None):
    """Plot success/failure patterns over time."""
    if not requests_data:
        print(f"No data to plot for {model_name}")
        return

    df = pd.DataFrame(requests_data)
    df['created_at'] = pd.to_datetime(df['created_at'])
    df = df.sort_values('created_at')

    # Create success flag
    df['success'] = df['status'] == 'completed'

    # Create figure
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8))
    fig.suptitle(f'{model_name} - Success Rate Analysis', fontsize=16, fontweight='bold')

    # Plot 1: Success/Failure over time
    colors = df['success'].map({True: 'green', False: 'red'})
    ax1.scatter(df['created_at'], df['success'], c=colors, alpha=0.6, s=50)
    ax1.set_ylabel('Success (1) / Failure (0)', fontsize=12)
    ax1.set_title('Request Success/Failure Timeline')
    ax1.set_yticks([0, 1])
    ax1.set_yticklabels(['Failed', 'Completed'])
    ax1.grid(True, alpha=0.3)
    ax1.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d %H:%M'))
    plt.setp(ax1.xaxis.get_majorticklabels(), rotation=45, ha='right')

    # Plot 2: Rolling success rate
    if len(df) >= 10:
        rolling_success = df.set_index('created_at')['success'].rolling(window=10, min_periods=1).mean() * 100
        ax2.plot(rolling_success.index, rolling_success.values, linewidth=2, color='blue')
        ax2.fill_between(rolling_success.index, rolling_success.values, alpha=0.3)

    overall_success = df['success'].mean() * 100
    ax2.axhline(overall_success, color='red', linestyle='--', linewidth=2,
               label=f"Overall: {overall_success:.1f}%")

    ax2.set_xlabel('Timestamp', fontsize=12)
    ax2.set_ylabel('Success Rate (%)', fontsize=12)
    ax2.set_title('10-Request Rolling Success Rate')
    ax2.set_ylim([0, 105])
    ax2.legend()
    ax2.grid(True, alpha=0.3)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d %H:%M'))
    plt.setp(ax2.xaxis.get_majorticklabels(), rotation=45, ha='right')

    plt.tight_layout()

    if output_dir:
        output_path = Path(output_dir) / f"{model_name.replace('/', '_')}_success_rate.png"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        print(f"Saved: {output_path}")
    else:
        plt.show()

    plt.close()


def plot_request_frequency(model_name, requests_data, output_dir=None):
    """Plot request frequency over time."""
    if not requests_data:
        print(f"No data to plot for {model_name}")
        return

    df = pd.DataFrame(requests_data)
    df['created_at'] = pd.to_datetime(df['created_at'])

    # Group by hour and count
    df['hour'] = df['created_at'].dt.floor('H')
    hourly_counts = df.groupby('hour').size()

    # Create figure
    fig, ax = plt.subplots(figsize=(12, 6))
    fig.suptitle(f'{model_name} - Request Frequency', fontsize=16, fontweight='bold')

    ax.bar(hourly_counts.index, hourly_counts.values, width=0.03, alpha=0.7, edgecolor='black')
    ax.set_xlabel('Timestamp', fontsize=12)
    ax.set_ylabel('Requests per Hour', fontsize=12)
    ax.set_title('Request Frequency Over Time')
    ax.grid(True, alpha=0.3, axis='y')
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d %H:%M'))
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha='right')

    plt.tight_layout()

    if output_dir:
        output_path = Path(output_dir) / f"{model_name.replace('/', '_')}_frequency.png"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        print(f"Saved: {output_path}")
    else:
        plt.show()

    plt.close()


def compare_providers(query, models_data, output_dir=None):
    """Compare the same model across different providers."""
    if len(models_data) < 2:
        print("Need at least 2 models to compare")
        return

    # Create comparison plots
    fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2, 2, figsize=(16, 12))
    fig.suptitle(f'Provider Comparison: "{query}"', fontsize=18, fontweight='bold')

    for model in models_data:
        if not model['requests']:
            continue

        model_name = f"{model['model_name']} ({model['provider']['name']})"
        df = pd.DataFrame(model['requests'])
        df['created_at'] = pd.to_datetime(df['created_at'])
        df = df.sort_values('created_at')

        # Plot 1: Processing time
        ax1.plot(df['created_at'], df['processing_time_ms'], marker='o', label=model_name, alpha=0.7)

        # Plot 2: Token usage
        ax2.plot(df['created_at'], df['total_tokens'], marker='s', label=model_name, alpha=0.7)

        # Plot 3: Success rate (rolling average)
        if len(df) >= 5:
            df['success'] = df['status'] == 'completed'
            rolling = df.set_index('created_at')['success'].rolling(window=5, min_periods=1).mean() * 100
            ax3.plot(rolling.index, rolling.values, label=model_name, linewidth=2, alpha=0.7)

        # Plot 4: Cost per request (if pricing available)
        if model.get('pricing_prompt') and model.get('pricing_completion'):
            df['cost'] = (df['input_tokens'] * model['pricing_prompt'] +
                         df['output_tokens'] * model['pricing_completion'])
            ax4.plot(df['created_at'], df['cost'], marker='^', label=model_name, alpha=0.7)

    # Configure axes
    ax1.set_ylabel('Processing Time (ms)')
    ax1.set_title('Processing Time Comparison')
    ax1.legend()
    ax1.grid(True, alpha=0.3)
    ax1.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d %H:%M'))
    plt.setp(ax1.xaxis.get_majorticklabels(), rotation=45, ha='right')

    ax2.set_ylabel('Total Tokens')
    ax2.set_title('Token Usage Comparison')
    ax2.legend()
    ax2.grid(True, alpha=0.3)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d %H:%M'))
    plt.setp(ax2.xaxis.get_majorticklabels(), rotation=45, ha='right')

    ax3.set_ylabel('Success Rate (%)')
    ax3.set_title('5-Request Rolling Success Rate')
    ax3.set_ylim([0, 105])
    ax3.legend()
    ax3.grid(True, alpha=0.3)
    ax3.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d %H:%M'))
    plt.setp(ax3.xaxis.get_majorticklabels(), rotation=45, ha='right')

    ax4.set_ylabel('Cost ($)')
    ax4.set_title('Cost per Request Comparison')
    ax4.legend()
    ax4.grid(True, alpha=0.3)
    ax4.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d %H:%M'))
    plt.setp(ax4.xaxis.get_majorticklabels(), rotation=45, ha='right')

    plt.tight_layout()

    if output_dir:
        output_path = Path(output_dir) / f"comparison_{query.replace(' ', '_')}.png"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        print(f"Saved: {output_path}")
    else:
        plt.show()

    plt.close()


def main():
    parser = argparse.ArgumentParser(description='Plot model chat completion statistics')
    parser.add_argument('--query', '-q', required=True, help='Search query (e.g., "gpt 4")')
    parser.add_argument('--provider', '-p', help='Optional provider filter')
    parser.add_argument('--output', '-o', help='Output directory for saving plots')
    parser.add_argument('--limit', '-l', type=int, default=5, help='Max models to plot')
    parser.add_argument('--requests-limit', type=int, default=1000, help='Max requests per model')
    parser.add_argument('--compare', action='store_true', help='Create comparison chart across providers')

    args = parser.parse_args()

    if not PLOTTING_AVAILABLE:
        print("Error: matplotlib and pandas are required for plotting")
        print("Install with: pip install matplotlib pandas")
        sys.exit(1)

    print(f"\nSearching for models matching '{args.query}'...")
    if args.provider:
        print(f"Filtering by provider: {args.provider}")

    # Search models
    results = search_chat_requests(
        query=args.query,
        provider_name=args.provider,
        requests_limit=args.requests_limit
    )

    if not results:
        print("No models found!")
        sys.exit(1)

    print(f"\nFound {len(results)} models\n")

    # Filter models with requests
    models_with_data = [m for m in results if m['requests']]

    if not models_with_data:
        print("No models have request data to plot!")
        sys.exit(1)

    print(f"Plotting data for {len(models_with_data)} models with request data...\n")

    # Create comparison chart if requested
    if args.compare and len(models_with_data) >= 2:
        print("Creating provider comparison chart...")
        compare_providers(args.query, models_with_data, args.output)

    # Create individual plots for each model
    for model in models_with_data:
        model_name = f"{model['model_name']} ({model['provider']['name']})"
        print(f"\nPlotting: {model_name}")
        print(f"  Total requests: {model['total_requests']}")
        print(f"  Plotting {len(model['requests'])} requests")

        # Generate all plots
        plot_tokens_over_time(model_name, model['requests'], args.output)
        plot_processing_time(model_name, model['requests'], args.output)
        plot_success_rate(model_name, model['requests'], args.output)
        plot_request_frequency(model_name, model['requests'], args.output)

    print(f"\n✓ All plots generated successfully!")
    if args.output:
        print(f"  Saved to: {args.output}")


if __name__ == '__main__':
    main()
