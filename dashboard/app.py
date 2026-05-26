import dash
import dash_bootstrap_components as dbc
from dash import Input, Output, State, callback_context

import bq_client
import layout
from tabs import session, trends, platform, alerts

app = dash.Dash(
    __name__,
    external_stylesheets=[dbc.themes.BOOTSTRAP],
    suppress_callback_exceptions=True,
)
app.layout = layout.build()


# Populate streamer dropdown on load
@app.callback(
    Output('streamer-filter', 'options'),
    Input('streamer-filter', 'id'),
)
def load_streamers(_):
    try:
        df = bq_client.get_streamers()
        return [{'label': row['streamer_name'], 'value': row['streamer_id']}
                for _, row in df.iterrows()]
    except Exception as e:
        print(f'[bq] get_streamers error: {e}')
        return []


# Render active tab
@app.callback(
    Output('tab-content', 'children'),
    Input('main-tabs', 'active_tab'),
    Input('refresh-btn', 'n_clicks'),
    State('date-range', 'start_date'),
    State('date-range', 'end_date'),
    State('streamer-filter', 'value'),
)
def render_tab(active_tab, _n, start_date, end_date, streamer_ids):
    start = start_date or '2026-06-11'
    end = end_date or '2026-07-20'
    sids = streamer_ids or None

    try:
        if active_tab == 'tab-session':
            df = bq_client.get_session_metrics(start, end, sids)
            return session.render(df)

        if active_tab == 'tab-trends':
            df_w = bq_client.get_weekly(sids)
            df_m = bq_client.get_monthly(sids)
            return trends.render(df_w, df_m)

        if active_tab == 'tab-platform':
            df = bq_client.get_platform_compare()
            return platform.render(df)

        if active_tab == 'tab-alerts':
            df_w = bq_client.get_weekly(sids)
            return alerts.render(df_w)

    except Exception as e:
        return dash.html.Div(
            f'Error loading data: {e}',
            className='text-danger p-3',
        )

    return dash.html.Div()


if __name__ == '__main__':
    app.run(debug=True, port=8050)
