import webbrowser
from pathlib import Path

import pandas as pd
import plotly.io as pio
import plotly.graph_objects as go
import config
import os
import re
from gs_handler import GoogleSheetsHandler, GSLink

def slugify(value):
    """
    Normalizes string, converts to lowercase, removes non-alpha characters,
    and converts spaces to hyphens.
    """
    # Remove all non-word characters (everything except numbers, letters, and underscore)
    # and also allow Hebrew characters.
    value = re.sub(r'[^\w\s\-\u0590-\u05FF]', '', value).strip().lower()
    # Replace one or more spaces or hyphens with a single hyphen
    value = re.sub(r'[-\s]+', '-', value)
    return value

if __name__ == '__main__':
    # DONT REMOVE THIS PART :
    # gsh = GoogleSheetsHandler(config.GOOGLE_API_USER, config.GOOGLE_WORKSHEET_ID)
    # gslink = GSLink(gsh)
    #
    # gslink.update_local(["Totals"], [config.web_totals_file], rows=5000, regular_data=False)
    # END OF NON REMOVAL COMMENT
    df = pd.read_csv(config.web_totals_file)
    df['תאריך'] = pd.to_datetime(df['תאריך'], dayfirst=False, format="mixed")
    df['YearMonth'] = df['תאריך'].dt.strftime('%Y-%m')

    # NEW: Create a directory for transaction detail pages if it doesn't exist
    transactions_dir = os.path.join(config.web_dir, 'transactions')
    os.makedirs(transactions_dir, exist_ok=True)
    print(f"Transaction details will be saved in: {transactions_dir}")

    # --- 2. Create Expenses Pivot Table ---
    expenses_df = df[df['בחובה'] > 0]
    expenses_pivot = pd.pivot_table(
        expenses_df,
        values='בחובה',
        index='YearMonth',
        columns='קטגוריה',
        aggfunc='sum'
    ).fillna(0)
    expenses_pivot.sort_index(ascending=False, inplace=True)

    # --- 3. Create Income Pivot Table ---
    income_df = df[df['בזכות'] > 0]
    income_pivot = pd.pivot_table(
        income_df,
        values='בזכות',
        index='YearMonth',
        columns='קטגוריה',
        aggfunc='sum'
    ).fillna(0)
    income_pivot.sort_index(ascending=False, inplace=True)

    # --- 4. Normalize Data for Color Scaling ---
    expenses_pivot_normalized = expenses_pivot.div(expenses_pivot.max(axis=0), axis=1).fillna(0)
    income_pivot_normalized = income_pivot.div(income_pivot.max(axis=0), axis=1).fillna(0)

    # --- NEW 5. Generate Transaction Detail Pages ---

    # Basic CSS for the detail pages for a clean look
    html_style = """
        <style>
            body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; margin: 2rem; background-color: #f9f9f9; direction: rtl; }
            h1 { color: #333; }
            table { border-collapse: collapse; width: 80%; margin: 1rem auto; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }
            th, td { padding: 12px 15px; text-align: right; border-bottom: 1px solid #ddd; }
            thead th { background-color: #4CAF50; color: white; }
            tbody tr:nth-child(even) { background-color: #f2f2f2; }
            tbody tr:hover { background-color: #ddd; }
        </style>
        """

    print("\nGenerating expense detail pages...")
    for year_month in expenses_pivot.index:
        for category in expenses_pivot.columns:
            if expenses_pivot.loc[year_month, category] > 0:
                # Filter the original dataframe for the specific transactions
                mask = (df['YearMonth'] == year_month) & (df['קטגוריה'] == category) & (df['בחובה'] > 0)
                details_df = df.loc[mask, ['תאריך', 'מקור עסקה', 'בחובה', 'תאור מורחב', 'פירוט נוסף']]

                # Create filename
                filename = f"{slugify(category)}_{year_month + "-01"}.html"
                filepath = os.path.join(transactions_dir, filename)

                # Generate HTML content
                html_content = f"""
                    <!DOCTYPE html>
                    <html lang="he">
                    <head>
                        <meta charset="UTF-8">
                        <title>פירוט עסקאות: {category} - {year_month}</title>
                        {html_style}
                    </head>
                    <body>
                        <h1>פירוט הוצאות עבור {category} ב-{year_month}</h1>
                        {details_df.to_html(index=False, classes='styled-table', float_format='%.2f')}
                    </body>
                    </html>
                    """

                # Save the file
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(html_content)

    print("Generating income detail pages...")
    for year_month in income_pivot.index:
        for category in income_pivot.columns:
            if income_pivot.loc[year_month, category] > 0:
                mask = (df['YearMonth'] == year_month) & (df['קטגוריה'] == category) & (df['בזכות'] > 0)
                details_df = df.loc[mask, ['תאריך', 'מקור עסקה', 'בזכות', 'תאור מורחב', 'פירוט נוסף']]
                filename = f"{slugify(category)}_{year_month + "-01"}.html"
                filepath = os.path.join(transactions_dir, filename)
                html_content = f"""
                    <!DOCTYPE html>
                    <html lang="he">
                    <head>
                        <meta charset="UTF-8">
                        <title>פירוט עסקאות: {category} - {year_month}</title>
                        {html_style}
                    </head>
                    <body>
                        <h1>פירוט הכנסות עבור {category} ב-{year_month}</h1>
                        {details_df.to_html(index=False, classes='styled-table', float_format='%.2f')}
                    </body>
                    </html>
                    """
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(html_content)

    # --- NEW 6. Define the JavaScript for Interactivity ---

    # This script will be injected into the final HTML files.
    # It listens for clicks, creates the correct filename, and opens the detail page.
    click_js = r"""
        
            var plot_div = document.getElementsByClassName('plotly-graph-div')[0];

            function slugify(text) {
                const a = 'àáâäæãåāăąçćčđďèéêëēėęěğǵḧîïíīįìłḿñńǹňôöòóœøōõőṕŕřßśšşșťțûüùúūǘůűųẃẍÿýžźż·/_,:;'
                const b = 'aaaaaaaaaacccddeeeeeeeegghiiiiiilmnnnnoooooooooprrsssssttuuuuuuuuuwxyyzzz------'
                const p = new RegExp(a.split('').join('|'), 'g')

                return text.toString().toLowerCase()
                    .replace(/\s+/g, '-') // Replace spaces with -
                    .replace(p, c => b.charAt(a.indexOf(c))) // Replace special characters
                    .replace(/&/g, '-and-') // Replace & with 'and'
                    .replace(/[^\w\-\u0590-\u05FF]+/g, '') // Remove all non-word chars except Hebrew
                    .replace(/\-\-+/g, '-') // Replace multiple - with single -
                    .replace(/^-+/, '') // Trim - from start of text
                    .replace(/-+$/, '') // Trim - from end of text
            }

            plot_div.on('plotly_click', function(data){
                var point = data.points[0];
                var category = point.x;
                var yearMonth = point.y;
                console.log(point)
                // var value = parseFloat(point.data.text.bdata.replace(/,/g, '')); // Get original value from text

                // if (value > 0) {
                var category_slug = slugify(category);
                var filename = `transactions/${category_slug}_${yearMonth}.html`;
                console.log(`Opening: ${filename}`);
                window.open(filename, '_blank');
                //} else {
                //    console.log("Clicked on a zero-value cell. No action taken.");
                //}
            });
        
        """

    # --- 7. Generate and Save Interactive Heatmaps (with click script) ---

    print("\nGenerating final interactive heatmaps...")

    # --- Expense Heatmap with Values ---
    fig_expenses = go.Figure(data=go.Heatmap(
        z=expenses_pivot_normalized,
        x=expenses_pivot.columns,
        y=expenses_pivot.index,
        text=expenses_pivot,
        texttemplate="%{text:,.2f}₪",
        hovertemplate=(
            "<b>חודש:</b> %{y}<br>"
            "<b>קטגוריה:</b> %{x}<br>"
        ),
        colorscale='Reds'
    ))

    fig_expenses.update_layout(
        title="<b>הוצאות חודשיות לפי קטגוריה (לחץ על תא לפירוט)</b><br><i>Monthly Expenses by Category (Click a Cell for Details)</i>",
        xaxis_title="קטגוריה (Category)",
        yaxis_title="חודש (Month)",
        xaxis_side="top"
    )

    # Save the expenses heatmap with the interactive script
    expenses_output_path = config.expenses_web_file
    pio.write_html(fig_expenses, expenses_output_path, post_script=click_js)  # NEW: added post_script
    print(f"Expenses heatmap saved to: {expenses_output_path}")

    # --- Income Heatmap with Values ---
    fig_income = go.Figure(data=go.Heatmap(
        z=income_pivot_normalized,
        x=income_pivot.columns,
        y=income_pivot.index,
        text=income_pivot,
        texttemplate="%{text:,.2f}₪",
        hovertemplate=(
            "<b>חודש:</b> %{y}<br>"
            "<b>קטגוריה:</b> %{x}<br>"
        ),
        colorscale='Greens'
    ))

    fig_income.update_layout(
        title="<b>הכנסות חודשיות לפי קטגוריה (לחץ על תא לפירוט)</b><br><i>Monthly Income by Category (Click a Cell for Details)</i>",
        xaxis_title="קטגוריה (Category)",
        yaxis_title="חודש (Month)",
        xaxis_side="top"
    )

    # Save the income heatmap with the interactive script
    income_output_path = config.incomes_web_file
    pio.write_html(fig_income, income_output_path, post_script=click_js)  # NEW: added post_script
    print(f"Income heatmap saved to: {income_output_path}")

    print("\nProcess complete! ✨")

    # Create a Path object for the HTML file
    pages = [config.incomes_web_file, config.expenses_web_file]
    for page in pages:
        file_path = Path(page)

        # Get the absolute path and convert it to a file URI
        # The .resolve() method gets the full absolute path.
        # The .as_uri() method correctly formats it as 'file:///C:/.../my_page.html'
        uri = file_path.resolve().as_uri()

        # Open the URI in the default web browser
        webbrowser.open(uri)

        print(f"Opening {uri} in your browser...")