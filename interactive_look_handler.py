import webbrowser
from pathlib import Path
import numpy as np
import pandas as pd
import plotly.io as pio
import plotly.graph_objects as go
import os
import re
import shutil

import config
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

    def __init__(self, data_file, web_dir, config):
        """
        Initializes the generator with data and configuration.

        Args:
            data_file (str): Path to the input CSV file.
            web_dir (str): Path to the root directory for web output.
            config: A configuration object containing output file paths.
        """
        self.data_file = Path(data_file)
        self.web_dir = Path(web_dir)
        self.config = config

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

    def run(self):
        """
        Executes the full report generation pipeline.
        """
        print("🚀 Starting report generation process...")
        self._load_and_prepare_data()
        self._prepare_directories()
        self._create_pivot_tables()
        self._normalize_pivots()
        self._generate_all_detail_pages()
        self._generate_all_heatmaps()
        self.open_reports()
        print("\nProcess complete! ✨")

    def _load_and_prepare_data(self):
        """Loads the CSV and performs initial data transformations."""
        print("1. Loading and preparing data...")
        self.df = pd.read_csv(self.data_file)
        self.df['תאריך'] = pd.to_datetime(self.df['תאריך'], dayfirst=False, format="mixed")
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
        """Returns the CSS style for detail pages."""
        return """
        <style>
            body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; margin: 2rem; background-color: #f9f9f9; direction: rtl; }
            h1, h2 { color: #333; }
            h1 { text-align: center; }
            h2 { border-bottom: 2px solid #4CAF50; padding-bottom: 5px; margin-top: 2rem;}
            table { border-collapse: collapse; width: 90%; margin: 1rem auto; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }
            th, td { padding: 12px 15px; text-align: right; border-bottom: 1px solid #ddd; }
            thead th { background-color: #4CAF50; color: white; }
            tbody tr:nth-child(even) { background-color: #f2f2f2; }
            tbody tr:hover { background-color: #e7e7e7; }
            .no-data { text-align: center; color: #888; margin-top: 1rem; }
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
                    filename = f"{slugify(category)}_{year_month+ "-01"}.html"
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
                    # Filter income and expense data for the specific cell
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

                    filename = f"{slugify(category)}_{year_month+ "-01"}.html"
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
            var category_slug = slugify(category);
            var filename = `transactions/{subfolder}/${{category_slug}}_${{yearMonth}}.html`;
            console.log(`Opening: ${{filename}}`);
            window.open(filename, '_blank');
        }});
        """

    def _create_and_save_heatmap(self, z_data, text_data, title, output_path, post_script, colorscale, custom_data=None,
                                 zmid=None, hovertemplate=None, colorbar_title=None):
        """Generic helper to create and save a Plotly heatmap."""
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
            title=f"<b>{title}</b><br><i>Click a Cell for Details</i>",
            xaxis_title="קטגוריה",
            yaxis_title="חודש",
            xaxis_side="top"
        )
        pio.write_html(fig, output_path, post_script=post_script)
        print(f"   - Heatmap saved to: {output_path}")

    def _generate_all_heatmaps(self):
        """Generates and saves all the interactive heatmaps."""
        print("6. Generating interactive heatmaps...")
        # Expense Heatmap
        self._create_and_save_heatmap(
            z_data=self.expenses_pivot_log,
            text_data=self.expenses_pivot,
            title="הוצאות חודשיות לפי קטגוריה",
            output_path=self.config.expenses_web_file,
            post_script=self._get_click_js('expense'),
            colorscale='Reds'
        )
        # Income Heatmap
        self._create_and_save_heatmap(
            z_data=self.income_pivot_log,
            text_data=self.income_pivot,
            title="הכנסות חודשיות לפי קטגוריה",
            output_path=self.config.incomes_web_file,
            post_script=self._get_click_js('income'),
            colorscale='Greens'
        )
        # Net Heatmap
        net_output_path = self.web_dir / 'net_heatmap.html'
        self._create_and_save_heatmap(
            z_data=self.net_pivot_normalized,
            text_data=self.net_pivot,
            title="הכנסות נטו (הכנסות פחות הוצאות) לפי קטגוריה",
            output_path=net_output_path,
            post_script=self._get_click_js('net'),
            colorscale='RdBu',
            # customdata=self.net_pivot,
            zmid=0,
            hovertemplate="<b>חודש:</b> %{y}<br><b>קטגוריה:</b> %{x}<br><b>נטו:</b> %{text:,.0f}₪<extra></extra>",
            colorbar_title='עוצמה יחסית<br>Relative Intensity'
        )

    def open_reports(self):
        """Opens the generated primary HTML reports in the default web browser."""
        print("7. Opening reports in browser...")
        net_output_path = self.web_dir / 'net_heatmap.html'
        pages_to_open = [self.config.incomes_web_file, self.config.expenses_web_file, net_output_path]
        for page in pages_to_open:
            uri = Path(page).resolve().as_uri()
            print(f"   - Opening {uri}")
            webbrowser.open(uri)


if __name__ == '__main__':
    gsh = GoogleSheetsHandler(config.GOOGLE_API_USER, config.GOOGLE_WORKSHEET_ID)
    gslink = GSLink(gsh)

    gslink.update_local(["Totals"], [config.web_totals_file], rows=5000, regular_data=False)

    report_generator = InteractiveReportGenerator(
        data_file=config.web_totals_file,
        web_dir=config.web_dir,
        config=config
    )
    report_generator.run()