"""Plotly figures in the dashboard's palette."""

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import plotly.io as pio

PINE = "#18322C"
GREEN = "#1D7558"
AMBER = "#E2A230"
SKY = "#3B78A8"
SLATE = "#6B7A80"
MIST = "#E9F0EC"
GRIDLINE = "#DCE5E0"
COLORWAY = [GREEN, AMBER, SKY, "#7D5BA6", "#4FA597", "#A9A245", "#C2667F", SLATE,
            "#2F5D50", "#B9822A", "#2C5B80", "#5E4580"]
STATUS_COLORS = {"Included": GREEN, "Excluded": "#B4544A", "Not reviewed": "#B8C4BE"}
IDEA_COLORS = {"Has an idea": AMBER, "No idea yet": SKY, "Yes": AMBER, "No": SKY}
FONT = "Figtree, sans-serif"

pio.templates["ctc"] = go.layout.Template(layout=go.Layout(
    font=dict(family=FONT, color=PINE, size=13),
    colorway=COLORWAY,
    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
    margin=dict(l=8, r=8, t=48, b=8),
    title=dict(font=dict(size=15, family=FONT, color=PINE), x=0, xanchor="left"),
    xaxis=dict(gridcolor=GRIDLINE, zeroline=False, linecolor=GRIDLINE),
    yaxis=dict(gridcolor=GRIDLINE, zeroline=False, linecolor=GRIDLINE),
    legend=dict(orientation="h", yanchor="top", y=-0.12, x=0, title_text=""),
    hoverlabel=dict(font_family=FONT, bgcolor="white"),
    polar=dict(bgcolor="rgba(0,0,0,0)",
               radialaxis=dict(gridcolor=GRIDLINE, linecolor=GRIDLINE, range=[0, 1], showticklabels=False),
               angularaxis=dict(gridcolor=GRIDLINE, linecolor=GRIDLINE)),
))
pio.templates.default = "ctc"


def _done(fig, height=320):
    fig.update_layout(height=height)
    return fig


def bar(df, x, y, title, color=None, orientation="v", height=320, color_map=None, text=None):
    fig = px.bar(df, x=x, y=y, color=color, orientation=orientation, title=title,
                 color_discrete_map=color_map, text=text)
    fig.update_traces(marker_line_width=0)
    if text:
        fig.update_traces(textposition="outside", cliponaxis=False)
    if orientation == "h":
        fig.update_yaxes(categoryorder="total ascending", title=None)
        fig.update_xaxes(title=None)
    else:
        fig.update_xaxes(title=None)
        fig.update_yaxes(title=None)
    return _done(fig, height)


def cumulative(dates, title):
    s = pd.to_datetime(pd.Series(dates), errors="coerce", utc=True).dropna().sort_values()
    if s.empty:
        return None
    df = pd.DataFrame({"date": s.dt.tz_convert(None), "Applications": np.arange(1, len(s) + 1)})
    fig = px.area(df, x="date", y="Applications", title=title, line_shape="hv")
    fig.update_traces(line_color=GREEN, fillcolor="rgba(29,117,88,0.15)")
    fig.update_xaxes(title=None)
    fig.update_yaxes(title=None)
    return _done(fig)


def world(df, title):
    fig = px.choropleth(df, locations="iso3", color="People", hover_name="Country",
                        color_continuous_scale=[[0, "#DCEBE3"], [0.4, "#6FB396"], [1, GREEN]], title=title)
    fig.update_geos(showframe=False, showcoastlines=False, projection_type="natural earth",
                    landcolor="#EEF2EF", bgcolor="rgba(0,0,0,0)", showcountries=True, countrycolor="#FFFFFF")
    fig.update_layout(coloraxis_showscale=False)
    return _done(fig, 340)


def radar(series, axes, title, height=360, fill=True, hover=None):
    """series: list of (name, values, color). hover: optional {axis: explanation}."""
    import textwrap
    fig = go.Figure()
    for name, values, color in series:
        v = list(values) + [values[0]]
        a = list(axes) + [axes[0]]
        notes = [("<br>" + "<br>".join(textwrap.wrap(hover[x], 60))) if hover and x in hover else "" for x in a]
        fig.add_trace(go.Scatterpolar(
            r=v, theta=a, name=name, line=dict(color=color, width=2), customdata=notes,
            fill="toself" if fill else None, opacity=0.85,
            fillcolor=_rgba(color, 0.18) if fill else None,
            hovertemplate="<b>%{theta}: %{r:.2f}</b>%{customdata}<extra>" + name + "</extra>"))
    fig.update_layout(title=dict(text=title, y=0.98, yanchor="top"), showlegend=len(series) > 1,
                      margin=dict(l=40, r=40, t=70, b=20),
                      legend=dict(orientation="h", yanchor="top", y=-0.08, x=0.5, xanchor="center"))
    fig.update_polars(radialaxis=dict(range=[0, 1]), domain=dict(y=[0.02, 0.92]))
    return _done(fig, height)


def heatmap(z, x, y, title, height=420):
    fig = go.Figure(go.Heatmap(z=z, x=x, y=y, colorscale=[[0, "#F2F6F3"], [0.5, "#8CC3AA"], [1, GREEN]],
                               hovertemplate="%{y} / %{x}: %{z}<extra></extra>", showscale=False))
    fig.update_layout(title=title)
    fig.update_xaxes(tickangle=-35, side="bottom")
    fig.update_yaxes(autorange="reversed")
    return _done(fig, height)


def _rgba(hex_color, alpha):
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def skill_hover():
    """Hover text for skill axes: what each one is detected from."""
    import config as C
    return {k.title(): "Found from keywords such as " + ", ".join(v[:6]) + "." for k, v in C.SKILLS.items()}
