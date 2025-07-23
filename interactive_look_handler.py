import pandas as pd
import plotly.io as pio
import plotly.graph_objects as go
import config
from gs_handler import GoogleSheetsHandler, GSLink

if __name__ == '__main__':
    # gsh = GoogleSheetsHandler(config.GOOGLE_API_USER, config.GOOGLE_WORKSHEET_ID)
    # gslink = GSLink(gsh)
    #
    # gslink.update_local(["Totals"], [config.web_totals_file], rows=5000, regular_data=False)
    df = pd.read_csv(config.web_totals_file)
    df['תאריך'] = pd.to_datetime(df['תאריך'], dayfirst=False, format="mixed")
    df['YearMonth'] = df['תאריך'].dt.strftime('%Y-%m')

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
    # We divide each column by its own maximum value. This makes the color scale relative per category.
    expenses_pivot_normalized = expenses_pivot.div(expenses_pivot.max(axis=0), axis=1).fillna(0)
    income_pivot_normalized = income_pivot.div(income_pivot.max(axis=0), axis=1).fillna(0)

    # --- 5. Generate and Save Interactive Heatmaps ---

    # --- Expense Heatmap with Values ---
    fig_expenses = go.Figure(data=go.Heatmap(
        z=expenses_pivot_normalized,  # Use NORMALIZED data for the color scale
        x=expenses_pivot.columns,  # Categories
        y=expenses_pivot.index,  # YearMonth
        text=expenses_pivot,  # Use ORIGINAL data for the text inside cells
        texttemplate="%{text:,.0f}",  # Format the text: comma separator, 0 decimal places
        hovertemplate=(
            "<b>חודש:</b> %{y}<br>"
            "<b>קטגוריה:</b> %{x}<br>"
            "<b>סכום:</b> %{text:,.2f} ₪<extra></extra>"
        ),
        colorscale='Reds'
    ))

    fig_expenses.update_layout(
        title="<b>הוצאות חודשיות לפי קטגוריה (ערכים מוצגים)</b><br><i>Monthly Expenses by Category (Values Displayed)</i>",
        xaxis_title="קטגוריה (Category)",
        yaxis_title="חודש (Month)",
        xaxis_side="top"  # Move category labels to the top
    )

    # Save the expenses heatmap
    expenses_output_path = config.web_dir + 'expenses_heatmap_with_values.html'
    pio.write_html(fig_expenses, expenses_output_path)
    print(f"Expenses heatmap saved to: {expenses_output_path}")

    # --- Income Heatmap with Values ---
    fig_income = go.Figure(data=go.Heatmap(
        z=income_pivot_normalized,  # Use NORMALIZED data for the color scale
        x=income_pivot.columns,  # Categories
        y=income_pivot.index,  # YearMonth
        text=income_pivot,  # Use ORIGINAL data for the text inside cells
        texttemplate="%{text:,.0f}",  # Format the text: comma separator, 0 decimal places
        hovertemplate=(
            "<b>חודש:</b> %{y}<br>"
            "<b>קטגוריה:</b> %{x}<br>"
            "<b>סכום:</b> %{text:,.2f} ₪<extra></extra>"
        ),
        colorscale='Greens'
    ))

    fig_income.update_layout(
        title="<b>הכנסות חודשיות לפי קטגוריה (ערכים מוצגים)</b><br><i>Monthly Income by Category (Values Displayed)</i>",
        xaxis_title="קטגוריה (Category)",
        yaxis_title="חודש (Month)",
        xaxis_side="top"  # Move category labels to the top
    )

    # Save the income heatmap
    income_output_path = config.web_dir + 'income_heatmap_with_values.html'
    pio.write_html(fig_income, income_output_path)
    print(f"Income heatmap saved to: {income_output_path}")