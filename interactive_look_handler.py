import webbrowser
from pathlib import Path
import numpy as np
import pandas as pd
import plotly.io as pio
import plotly.graph_objects as go
import os
import re
import shutil
import matplotlib.colors as mcolors
import matplotlib.cm as mcm
import matplotlib as mpl
# Assuming 'config', 'gs_handler' are in the same directory or accessible
import config
from compile_handler import parse_post_ingest_date_column
from gs_handler import GoogleSheetsHandler, GSLink


def slugify(value):
    """
    Normalizes a string by converting it to lowercase, removing non-alpha
    characters, and converting spaces to hyphens. Handles Hebrew characters.
    """
    value = re.sub(r'[^\w\s\-\u0590-\u05FF]', '', value).strip().lower()
    value = re.sub(r'[-\s]+', '-', value)
    return value


class InteractiveReportGenerator:
    """
    Generates interactive financial reports including heatmaps and detailed
    transaction pages from a CSV data file.
    """

    def __init__(self, data_file, web_dir, _config):
        """
        Initializes the generator with data and configuration.

        Args:
            data_file (str): Path to the input CSV file.
            web_dir (str): Path to the root directory for web output.
            _config: A configuration object containing output file paths.
        """
        self.data_file = Path(data_file)
        self.web_dir = Path(web_dir)
        self.config = _config

        # Paths for generated detail pages
        self.base_transactions_dir = self.web_dir / 'transactions'
        self.expense_transactions_dir = self.base_transactions_dir / 'expense'
        self.income_transactions_dir = self.base_transactions_dir / 'income'
        self.net_transactions_dir = self.base_transactions_dir / 'net'

        # DataFrames will be populated by helper methods
        self.df = None
        self.expenses_pivot = None
        self.income_pivot = None
        self.net_pivot = None
        self.expenses_pivot_log = None
        self.income_pivot_log = None
        self.net_pivot_normalized = None

        # DataFrames for summary statistics
        self.expense_summary = None
        self.income_summary = None
        self.net_summary = None

        # --- NEW: Define all possible statistical operations ---
        self._define_stat_operations()

    def _define_stat_operations(self):
        """Defines a dictionary of available statistical operations."""
        self.STAT_DEFINITIONS = {
            'total': {
                'name': 'סך הכל (Total)',
                'func': lambda p, axis, rt: p.sum(axis=axis)
            },
            'mean': {
                # Name depends on the axis (by_cat vs by_month)
                'name_by_cat': 'ממוצע חודשי (Avg)',
                'name_by_month': 'ממוצע לקטגוריה (Avg)',
                'func': lambda p, axis, rt: p.mean(axis=axis)
            },
            'std': {
                'name': 'סטיית תקן (Std Dev)',
                'func': lambda p, axis, rt: p.std(axis=axis)
            },
            'median': {
                'name': 'חציון (Median)',
                'func': lambda p, axis, rt: p.median(axis=axis)
            },
            'max': {
                'name': 'מקסימום (Max)',
                'func': lambda p, axis, rt: p.max(axis=axis)
            },
            'min': {
                'name': 'מינימום (Min)',
                # Special function to handle non-zero min for income/expense
                'func': lambda p, axis, rt: p[p > 0].min(axis=axis) if rt in ['expense', 'income'] else p.min(axis=axis)
            },
            'p25': {
                'name': 'אחוזון 25 (25th Pctl)',
                'func': lambda p, axis, rt: p.quantile(0.25, axis=axis)
            },
            'p75': {
                'name': 'אחוזון 75 (75th Pctl)',
                'func': lambda p, axis, rt: p.quantile(0.75, axis=axis)
            },
            'count': {
                'name': 'ספירה (Count > 0)',
                'func': lambda p, axis, rt: (p != 0).sum(axis=axis)
            }
        }
    def run(self):
        """
        Executes the full report generation pipeline.
        """
        print("🚀 Starting report generation process...")
        self._load_and_prepare_data()
        self._prepare_directories()
        self._create_pivot_tables()
        self._calculate_summary_statistics()
        self._normalize_pivots()
        self._generate_all_detail_pages()
        self._generate_all_report_pages()
        self.open_reports()
        print("\nProcess complete! ✨")

    def _load_and_prepare_data(self):
        """Loads the CSV and performs initial data transformations."""
        print("1. Loading and preparing data...")
        self.df = pd.read_csv(self.data_file)
        self.df['תאריך'] = parse_post_ingest_date_column(self.df['תאריך'])
        self.df['YearMonth'] = self.df['תאריך'].dt.strftime('%Y-%m')

    def _prepare_directories(self):
        """Creates a clean set of directories for the report files."""
        print(f"2. Setting up output directories in: {self.web_dir}")
        if self.base_transactions_dir.exists():
            shutil.rmtree(self.base_transactions_dir)
            if not self.base_transactions_dir.exists():
                print(f"Removed {self.base_transactions_dir} directory recursively.")
        self.expense_transactions_dir.mkdir(parents=True, exist_ok=True)
        self.income_transactions_dir.mkdir(parents=True, exist_ok=True)
        self.net_transactions_dir.mkdir(parents=True, exist_ok=True)

    def _create_pivot_tables(self):
        """Generates pivot tables for expenses, income, and net flow."""
        print("3. Creating pivot tables...")
        # Expense Pivot
        expenses_df = self.df[self.df['בחובה'] > 0]
        self.expenses_pivot = pd.pivot_table(
            expenses_df, values='בחובה', index='YearMonth', columns='קטגוריה', aggfunc='sum'
        ).fillna(0).sort_index(ascending=False)

        # Income Pivot
        income_df = self.df[self.df['בזכות'] > 0]
        self.income_pivot = pd.pivot_table(
            income_df, values='בזכות', index='YearMonth', columns='קטגוריה', aggfunc='sum'
        ).fillna(0).sort_index(ascending=False)

        # Net Pivot
        all_cols = self.expenses_pivot.columns.union(self.income_pivot.columns)
        all_idx = self.expenses_pivot.index.union(self.income_pivot.index)
        income_aligned = self.income_pivot.reindex(index=all_idx, columns=all_cols).fillna(0)
        expenses_aligned = self.expenses_pivot.reindex(index=all_idx, columns=all_cols).fillna(0)
        self.net_pivot = (income_aligned - expenses_aligned).sort_index(ascending=False)

    def _calculate_stats(self, pivot, report_type='expense', stats_to_calculate=None):
        """
        Calculates a customizable set of summary statistics for a pivot table.

        Args:
            pivot (pd.DataFrame): The pivot table to analyze.
            report_type (str): Type of report ('expense', 'income', 'net').
            stats_to_calculate (list, optional): A list of stat keys from
                                                 self.STAT_DEFINITIONS.
                                                 Defaults to a standard set.
        """
        # If no list is provided, use a default set of comprehensive stats
        if stats_to_calculate is None:
            stats_to_calculate = ['total', 'mean', 'std', 'median', 'max', 'min', 'p75', 'p25', 'count']

        # --- Stats by Category (columns, axis=0) ---
        stats_by_cat = pd.DataFrame(index=pivot.columns)
        cat_column_order = []
        for stat_key in stats_to_calculate:
            if stat_key in self.STAT_DEFINITIONS:
                stat_info = self.STAT_DEFINITIONS[stat_key]
                # Use specific name for category axis if available (e.g., for 'mean')
                col_name = stat_info.get('name_by_cat', stat_info.get('name'))
                stats_by_cat[col_name] = stat_info['func'](pivot, 0, report_type)
                cat_column_order.append(col_name)

        # --- Stats by Month (rows, axis=1) ---
        stats_by_month = pd.DataFrame(index=pivot.index)
        month_column_order = []
        for stat_key in stats_to_calculate:
            if stat_key in self.STAT_DEFINITIONS:
                stat_info = self.STAT_DEFINITIONS[stat_key]
                # Use specific name for month axis if available (e.g., for 'mean')
                col_name = stat_info.get('name_by_month', stat_info.get('name'))
                stats_by_month[col_name] = stat_info['func'](pivot, 1, report_type)
                month_column_order.append(col_name)

        return {
            'by_category': stats_by_cat.reindex(columns=cat_column_order).fillna(0),
            'by_month': stats_by_month.reindex(columns=month_column_order).fillna(0)
        }

        # --- MODIFIED: Define your desired stats list here ---
    def _calculate_summary_statistics(self):
        """Calculates and stores summary statistics for all pivot tables."""
        print("3.5. Calculating summary statistics...")

        # ✅ CHOOSE YOUR STATS HERE! ✅
        # Available keys: 'total', 'mean', 'std', 'median', 'max', 'min', 'p75', 'p25', 'count'
        desired_stats = [
            'total',
            'mean',
            'median',
            # 'max',
            # 'min',
            # 'std',
            # 'count'
        ]

        print(f"   - Calculating stats: {desired_stats}")

        self.expense_summary = self._calculate_stats(self.expenses_pivot, 'expense', desired_stats)
        self.income_summary = self._calculate_stats(self.income_pivot, 'income', desired_stats)
        self.net_summary = self._calculate_stats(self.net_pivot, 'net', desired_stats)

    def _normalize_pivots(self):
        """Normalizes pivot table data for color scaling in heatmaps."""
        print("4. Normalizing data for visualization...")
        self.expenses_pivot_log = np.log1p(self.expenses_pivot)
        self.income_pivot_log = np.log1p(self.income_pivot)

        # For net data, use symmetric log and normalize each column independently
        net_pivot_symlog = np.sign(self.net_pivot) * np.log1p(np.abs(self.net_pivot))

        def normalize_col(col):
            max_abs = col.abs().max()
            return col / max_abs if max_abs != 0 else col

        self.net_pivot_normalized = net_pivot_symlog.apply(normalize_col).fillna(0)

    def _get_html_style(self):
        """Returns the CSS style for detail pages and report pages."""
        return """
        <style>
            body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; margin: 2rem; background-color: #f9f9f9; direction: rtl; }
            h1, h2 { color: #333; }
            h1 { text-align: center; }
            h2 { border-bottom: 2px solid #4CAF50; padding-bottom: 5px; margin-top: 2rem;}
            table.styled-table { border-collapse: collapse; width: 100%; margin: 1rem auto; box-shadow: 0 2px 5px rgba(0,0,0,0.1); font-size: 0.9em; }
            .styled-table th, .styled-table td { padding: 10px 12px; text-align: right; border: 1px solid #ddd; }
            .styled-table thead th { background-color: #4CAF50; color: white; }
            .styled-table tbody tr:hover { background-color: #f0f0f0 !important; }
            .no-data { text-align: center; color: #888; margin-top: 1rem; }
            .plotly-graph-div { margin: 2rem auto; }
            .stats-container { display: flex; flex-wrap: wrap; justify-content: space-around; gap: 2rem; width: 100%; margin-top: 2rem; }
            .stats-table-container { flex: 1; min-width: 48%; max-width: 100%; overflow-x: auto;}
        </style>
        """

    def _generate_all_detail_pages(self):
        """Generates all HTML detail pages for expenses, income, and net."""
        print("5. Generating HTML detail pages...")
        self._generate_simple_detail_pages(
            pivot=self.expenses_pivot,
            type_name="הוצאות",
            value_col='בחובה',
            output_dir=self.expense_transactions_dir
        )
        self._generate_simple_detail_pages(
            pivot=self.income_pivot,
            type_name="הכנסות",
            value_col='בזכות',
            output_dir=self.income_transactions_dir
        )
        self._generate_net_detail_pages()

    def _generate_simple_detail_pages(self, pivot, type_name, value_col, output_dir):
        """Helper to generate detail pages for simple cases (income/expense)."""
        print(f"   - Generating {type_name.lower()} pages...")
        cols_to_show = ['תאריך', 'מקור עסקה', value_col, 'תאור מורחב', 'פירוט נוסף']
        for year_month in pivot.index:
            for category in pivot.columns:
                if pivot.loc[year_month, category] > 0:
                    mask = (self.df['YearMonth'] == year_month) & \
                           (self.df['קטגוריה'] == category) & \
                           (self.df[value_col] > 0)
                    details_df = self.df.loc[mask, cols_to_show]
                    filename = f"{slugify(category)}_{year_month}.html"
                    filepath = output_dir / filename
                    html_content = f"""
                        <!DOCTYPE html><html lang="he"><head><meta charset="UTF-8">
                        <title>פירוט {type_name}: {category} - {year_month}</title>{self._get_html_style()}</head>
                        <body><h1>פירוט {type_name} עבור {category} ב-{year_month}</h1>
                        {details_df.to_html(index=False, classes='styled-table', float_format='%.2f')}
                        </body></html>"""
                    with open(filepath, 'w', encoding='utf-8') as f:
                        f.write(html_content)

    def _generate_net_detail_pages(self):
        """Generates detail pages for the net report, showing both income and expenses."""
        print(f"   - Generating net pages...")
        for year_month in self.net_pivot.index:
            for category in self.net_pivot.columns:
                if self.net_pivot.loc[year_month, category] != 0:
                    income_mask = (self.df['YearMonth'] == year_month) & (self.df['קטגוריה'] == category) & (
                            self.df['בזכות'] > 0)
                    expense_mask = (self.df['YearMonth'] == year_month) & (self.df['קטגוריה'] == category) & (
                            self.df['בחובה'] > 0)
                    income_df = self.df.loc[income_mask, ['תאריך', 'מקור עסקה', 'בזכות', 'תאור מורחב', 'פירוט נוסף']]
                    expense_df = self.df.loc[expense_mask, ['תאריך', 'מקור עסקה', 'בחובה', 'תאור מורחב', 'פירוט נוסף']]
                    income_html = income_df.to_html(index=False, classes='styled-table',
                                                    float_format='%.2f') if not income_df.empty else "<p class='no-data'>אין הכנסות רשומות</p>"
                    expense_html = expense_df.to_html(index=False, classes='styled-table',
                                                      float_format='%.2f') if not expense_df.empty else "<p class='no-data'>אין הוצאות רשומות</p>"
                    filename = f"{slugify(category)}_{year_month}.html"
                    filepath = self.net_transactions_dir / filename
                    html_content = f"""
                        <!DOCTYPE html><html lang="he"><head><meta charset="UTF-8">
                        <title>פירוט נטו: {category} - {year_month}</title>{self._get_html_style()}</head>
                        <body><h1>פירוט תנועות עבור {category} ב-{year_month}</h1>
                        <h2>הכנסות</h2>{income_html}
                        <h2>הוצאות</h2>{expense_html}</body></html>"""
                    with open(filepath, 'w', encoding='utf-8') as f:
                        f.write(html_content)

    def _get_click_js(self, subfolder):
        """Returns the Plotly JavaScript for handling click events."""
        return f"""
        var plot_div = document.getElementsByClassName('plotly-graph-div')[0];
        function slugify(text) {{
            const a = 'àáâäæãåāăąçćčđďèéêëēėęěğǵḧîïíīįìłḿñńǹňôöòóœøōõőṕŕřßśšşșťțûüùúūǘůűųẃẍÿýžźż·/_,:;';
            const b = 'aaaaaaaaaacccddeeeeeeeegghiiiiiilmnnnnoooooooooprrsssssttuuuuuuuuuwxyyzzz------';
            const p = new RegExp(a.split('').join('|'), 'g');
            return text.toString().toLowerCase()
                .replace(/\\s+/g, '-')
                .replace(p, c => b.charAt(a.indexOf(c)))
                .replace(/&/g, '-and-')
                .replace(/[^\\w\\-\\u0590-\\u05FF]+/g, '')
                .replace(/\\-\\-+/g, '-')
                .replace(/^-+/, '').replace(/-+$/, '');
        }}
        plot_div.on('plotly_click', function(data){{
            var point = data.points[0];
            var category = point.x;
            var yearMonth = point.y;
            yearMonth = yearMonth.split("-").slice(0,2).join("-"); // dont remove this part. point.x returns full date (YYYY-MM-DD), we need (YYYY-MM) for the accurate filenames
            var category_slug = slugify(category);
            var filename = `transactions/{subfolder}/${{category_slug}}_${{yearMonth}}.html`;
            console.log(`Opening: ${{filename}}`);
            window.open(filename, '_blank');
        }});
        """

    @staticmethod
    def _style_stats_table(stats_df, report_type='expense'):
        """
        Applies heatmap-style coloring with LOG SCALING to a statistics DataFrame
        using a custom apply function to ensure correctness.
        """
        if stats_df.empty:
            return "<p class='no-data'>No statistics to display.</p>"

        # Define colormap names based on report type
        if report_type == 'net':
            cm_seq = 'RdBu'
        elif report_type == 'income':
            cm_seq = 'Greens'
        else:  # 'expense'
            cm_seq = 'Reds'

        cm_variance = 'Oranges'
        cm_count = 'Purples'

        # Define the log transformation function
        if report_type == 'net':
            transform = lambda x: np.sign(x) * np.log1p(np.abs(x))
        else:
            transform = np.log1p

        def _apply_color_to_series(series, cmap_name, transform_func):
            """Helper to apply log-scaled colormap to a single column (Series)."""
            series_transformed = series.astype(float).map(transform_func)

            min_val, max_val = series_transformed.min(), series_transformed.max()
            if min_val == max_val:
                return [''] * len(series)

            norm = mcolors.Normalize(vmin=min_val, vmax=max_val)
            cmap = mpl.colormaps.get_cmap(cmap_name)

            colors = series_transformed.map(
                lambda x: mcolors.to_hex(cmap(norm(x))) if pd.notna(x) else ''
            )
            return 'background-color: ' + colors

        # Define the columns for each color scheme
        seq_cols = [
            'סך הכל (Total)', 'ממוצע חודשי (Avg)', 'ממוצע לקטגוריה (Avg)', 'חציון (Median)',
            'מקסימום (Max)', 'מינימום (Min)', 'אחוזון 75 (75th Pctl)', 'אחוזון 25 (25th Pctl)'
        ]

        # Determine which columns to apply styles to based on what exists in the df
        valid_seq_cols = [col for col in seq_cols if col in stats_df.columns]
        stylingDict = {col: '{:,.2f}₪' for col in valid_seq_cols}
        styler = stats_df.style

        # --- MODIFIED: Apply styles only if the columns exist ---
        if valid_seq_cols:
            styler = styler.apply(_apply_color_to_series, cmap_name=cm_seq, transform_func=transform,
                                  subset=valid_seq_cols, axis=0)
        if 'סטיית תקן (Std Dev)' in stats_df.columns:
            styler = styler.apply(_apply_color_to_series, cmap_name=cm_variance, transform_func=transform,
                                  subset=['סטיית תקן (Std Dev)'], axis=0)
        if 'ספירה (Count > 0)' in stats_df.columns:
            styler = styler.apply(_apply_color_to_series, cmap_name=cm_count, transform_func=transform,
                                  subset=['ספירה (Count > 0)'], axis=0)

        # Format the text that will be displayed
        styler = styler.format(stylingDict)
        #if 'ספירה (Count > 0)' in stats_df.columns:
        #    styler = styler.format({'ספירה (Count > 0)': '{:,.0f}'})

        # Set final table attributes
        styler = styler.set_table_attributes('class="styled-table"')

        return styler.to_html()
    def _create_and_save_report_page(self, z_data, text_data, title, output_path, post_script, colorscale,
                                     summary_stats, report_type, custom_data=None, zmid=None, hovertemplate=None,
                                     colorbar_title=None):
        """Generic helper to create and save a Plotly report page with a heatmap and summary stats."""
        fig = go.Figure(data=go.Heatmap(
            z=z_data,
            x=text_data.columns,
            y=text_data.index,
            text=text_data,
            texttemplate="%{text:,.0f}₪",
            colorscale=colorscale,
            customdata=custom_data,
            zmid=zmid,
            hovertemplate=hovertemplate,
            colorbar=dict(title=colorbar_title) if colorbar_title else None
        ))
        fig.update_layout(
            title=f"<b>{title}</b><br><i>Click a Cell for Transaction Details</i>",
            xaxis_title="קטגוריה",
            yaxis_title="חודש",
            xaxis_side="top",
            height=800  # Make the heatmap taller for better visibility
        )

        # Get components for the final HTML page
        plot_div = pio.to_html(fig, full_html=False, include_plotlyjs='cdn')
        stats_by_cat_html = self._style_stats_table(summary_stats['by_category'], report_type)
        stats_by_month_html = self._style_stats_table(summary_stats['by_month'], report_type)

        # Assemble the full HTML document
        html_content = f"""
        <!DOCTYPE html>
        <html lang="he">
        <head>
            <meta charset="UTF-8">
            <title>{title}</title>
            {self._get_html_style()}
        </head>
        <body>
            <h1>{title}</h1>
            {plot_div}
            <div class="stats-container">
                <div class="stats-table-container">
                    <h2>📊 סיכום לפי קטגוריה</h2>
                    {stats_by_cat_html}
                </div>
                <div class="stats-table-container">
                    <h2>📅 סיכום לפי חודש</h2>
                    {stats_by_month_html}
                </div>
            </div>
            <script>
                {post_script}
            </script>
        </body>
        </html>
        """

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
        print(f"   - Report page saved to: {output_path}")

    def _generate_all_report_pages(self):
        """Generates and saves all the interactive report pages."""
        print("6. Generating interactive report pages...")
        # Expense Report
        self._create_and_save_report_page(
            z_data=self.expenses_pivot_log,
            text_data=self.expenses_pivot,
            title="הוצאות חודשיות לפי קטגוריה",
            output_path=self.config.expenses_web_file,
            post_script=self._get_click_js('expense'),
            colorscale='Reds',
            summary_stats=self.expense_summary,
            report_type='expense'
        )
        # Income Report
        self._create_and_save_report_page(
            z_data=self.income_pivot_log,
            text_data=self.income_pivot,
            title="הכנסות חודשיות לפי קטגוריה",
            output_path=self.config.incomes_web_file,
            post_script=self._get_click_js('income'),
            colorscale='Greens',
            summary_stats=self.income_summary,
            report_type='income'
        )
        # Net Report
        net_output_path = self.web_dir / 'net_heatmap.html'
        self._create_and_save_report_page(
            z_data=self.net_pivot_normalized,
            text_data=self.net_pivot,
            title="הכנסות נטו (הכנסות פחות הוצאות) לפי קטגוריה",
            output_path=net_output_path,
            post_script=self._get_click_js('net'),
            colorscale='RdBu',
            zmid=0,
            hovertemplate="<b>חודש:</b> %{y}<br><b>קטגוריה:</b> %{x}<br><b>נטו:</b> %{text:,.0f}₪<extra></extra>",
            colorbar_title='עוצמה יחסית<br>Relative Intensity',
            summary_stats=self.net_summary,
            report_type='net'
        )

    def open_reports(self):
        """Opens the generated primary HTML reports in the default web browser."""
        print("7. Opening reports in browser...")
        net_output_path = self.web_dir / 'net_heatmap.html'
        pages_to_open = [self.config.incomes_web_file, self.config.expenses_web_file, net_output_path]
        for page in pages_to_open:
            uri = Path(page).resolve().as_uri()
            print(f"   - Opening {uri}")
            # webbrowser.open(uri)


if __name__ == '__main__':
    # Ensure Google API credentials and worksheet ID are set correctly in config.py
    gsh = GoogleSheetsHandler(config.GOOGLE_API_USER, config.GOOGLE_WORKSHEET_ID)
    gslink = GSLink(gsh)
    # This part pulls data from Google Sheets; ensure it's configured correctly
    gslink.update_local(["Totals"], [config.web_totals_file], rows=5000, regular_data=False)

    report_generator = InteractiveReportGenerator(
        data_file=config.web_totals_file,
        web_dir=config.web_dir,
        _config=config
    )
    report_generator.run()
