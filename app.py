import streamlit as st
import pandas as pd
import io
import re

st.title("Jornada Laboral Conductores")

# ==============================
# CONFIGURACIÓN (EN MINUTOS)
# ==============================

HORAS_MAX_JORNADA = st.number_input("Horas máximas jornada", value=8.0, step=0.1)

HORAS_DESCANSO_LARGO = st.number_input("Horas descanso largo", value=4.0, step=0.1)
MIN_PAUSA = st.number_input("Pausa mínima (minutos)", value=34, step=1)
MIN_PARADA = st.number_input("Duración mínima parada (minutos)", value=17, step=1)

HORAS_MIN_PAUSA = MIN_PAUSA / 60
UMBRAL_PARADA_MIN = MIN_PARADA / 60

# ==============================
# FUNCIONES UBICACIÓN PRO
# ==============================

def limpiar_ubicacion(texto):
    if pd.isna(texto):
        return ""
    texto = str(texto).lower().strip()
    texto = re.sub(r'\s+', ' ', texto)
    return texto

def calcular_ubic_principal(grupo):

    g = grupo.copy()
    g["ubic_limpia"] = g["ubicacion"].apply(limpiar_ubicacion)

    def peso_estado(row):
        if row["estado"] in ["ralenti", "apagado"]:
            return row["delta_horas"] * 2
        else:
            return row["delta_horas"]

    g["peso"] = g.apply(peso_estado, axis=1)

    resumen = g.groupby("ubic_limpia").agg({
        "delta_horas": "sum",
        "peso": "sum",
        "estado": "count"
    }).rename(columns={"estado": "frecuencia"})

    if len(resumen) == 0:
        return ""

    resumen["score"] = (
        resumen["peso"] * 0.7 +
        resumen["delta_horas"] * 0.2 +
        resumen["frecuencia"] * 0.1
    )

    mejor = resumen.sort_values("score", ascending=False).index[0]

    return mejor

# ==============================
# SUBIR ARCHIVOS
# ==============================

files = st.file_uploader("Sube archivos CSV", accept_multiple_files=True)

if files:

    lista_df = []

    for file in files:
        df_temp = pd.read_csv(file, sep=";", encoding="utf-8")

        df_temp.columns = df_temp.columns.str.strip()

        df_temp = df_temp.rename(columns={
            "Fecha y Hora": "fecha_hora",
            "Velocidad": "velocidad",
            "Ignicion*": "ignicion",
            "Conductor": "conductor",
            "Localización": "ubicacion"
        })

        df_temp["vehiculo"] = file.name[:6].upper()

        lista_df.append(df_temp)

    df = pd.concat(lista_df, ignore_index=True)

    # ==============================
    # LIMPIEZA
    # ==============================

    df["fecha_hora"] = pd.to_datetime(df["fecha_hora"], errors="coerce")

    df["ignicion"] = df["ignicion"].astype(str).str.strip().str.lower()
    df["ignicion_on"] = df["ignicion"].isin(["encendido"])

    df["velocidad"] = (
        df["velocidad"]
        .astype(str)
        .str.replace(",", ".", regex=False)
        .str.extract(r"(\d+\.?\d*)")[0]
    )

    df["velocidad"] = pd.to_numeric(df["velocidad"], errors="coerce").fillna(0)

    df = df.sort_values(by=["vehiculo", "fecha_hora"]).reset_index(drop=True)

    df["fecha"] = df["fecha_hora"].dt.date

    # ==============================
    # ESTADOS
    # ==============================

    def clasificar_estado(row):
        if row["ignicion_on"] and row["velocidad"] > 0:
            return "conduciendo"
        elif row["ignicion_on"] and row["velocidad"] == 0:
            return "ralenti"
        else:
            return "apagado"

    df["estado"] = df.apply(clasificar_estado, axis=1)

    # ==============================
    # TIEMPOS
    # ==============================

    df["fecha_siguiente"] = df.groupby("vehiculo")["fecha_hora"].shift(-1)

    df["delta_horas"] = (
        df["fecha_siguiente"] - df["fecha_hora"]
    ).dt.total_seconds() / 3600

    df["delta_horas"] = df["delta_horas"].fillna(0)

    # ==============================
    # BLOQUES
    # ==============================

    df["grupo"] = (df["estado"] != df["estado"].shift()).cumsum()

    bloques_list = []

    for (vehiculo, grupo), g in df.groupby(["vehiculo", "grupo"]):

        g = g.sort_values("fecha_hora")

        estado = g["estado"].iloc[0]
        inicio = g["fecha_hora"].iloc[0]
        fin = g["fecha_hora"].iloc[-1]
        duracion = g["delta_horas"].sum()

        ubic_inicio = g["ubicacion"].iloc[0] if "ubicacion" in g else ""
        ubic_fin = g["ubicacion"].iloc[-1] if "ubicacion" in g else ""

        bloques_list.append({
            "vehiculo": vehiculo,
            "grupo": grupo,
            "estado": estado,
            "inicio": inicio,
            "fin": fin,
            "duracion_horas": duracion,
            "ubic_inicio": ubic_inicio,
            "ubic_fin": ubic_fin
        })

    bloques = pd.DataFrame(bloques_list)

    # ==============================
    # KPIs
    # ==============================

    kpis_list = []

    for (vehiculo, fecha), grupo in df.groupby(["vehiculo", "fecha"]):

        conductor = grupo["conductor"].dropna().iloc[0] if "conductor" in grupo else "N/A"

        inicio_jornada = grupo.loc[grupo["ignicion_on"], "fecha_hora"].min()
        fin_jornada = grupo.loc[grupo["ignicion_on"], "fecha_hora"].max()

        horas_conduccion = grupo.loc[grupo["estado"] == "conduciendo", "delta_horas"].sum()
        horas_ralenti = grupo.loc[grupo["estado"] == "ralenti", "delta_horas"].sum()
        horas_trabajo = horas_conduccion + horas_ralenti

        # UBICACIONES
        ubic_inicio = grupo.loc[grupo["fecha_hora"] == inicio_jornada, "ubicacion"].iloc[0] if pd.notna(inicio_jornada) else ""
        ubic_fin = grupo.loc[grupo["fecha_hora"] == fin_jornada, "ubicacion"].iloc[0] if pd.notna(fin_jornada) else ""
        ubic_principal = calcular_ubic_principal(grupo)

        # ==============================
        # PARADAS + DESCANSOS
        # ==============================

        inicio_dia = pd.Timestamp(fecha)
        fin_dia = inicio_dia + pd.Timedelta(days=1)

        bloques_vehiculo = bloques[bloques["vehiculo"] == vehiculo]

        bloques_dia = bloques_vehiculo[
            (bloques_vehiculo["inicio"] < fin_dia) &
            (bloques_vehiculo["fin"] > inicio_dia)
        ]

        numero_paradas = 0
        horas_descanso = 0
        horas_pausa = 0

        for _, b in bloques_dia.iterrows():

            inicio_real = max(b["inicio"], inicio_dia)
            fin_real = min(b["fin"], fin_dia)

            if inicio_real < fin_real:

                horas = (fin_real - inicio_real).total_seconds() / 3600

                if b["estado"] in ["ralenti", "apagado"] and horas >= UMBRAL_PARADA_MIN:
                    numero_paradas += 1

                if b["estado"] == "apagado":
                    if horas >= HORAS_DESCANSO_LARGO:
                        horas_descanso += horas
                    elif horas >= HORAS_MIN_PAUSA:
                        horas_pausa += horas

        horas_extra = max(0, horas_trabajo - HORAS_MAX_JORNADA)

        kpis_list.append({
            "conductor": conductor,
            "vehiculo": vehiculo,
            "fecha": fecha,
            "inicio_jornada": inicio_jornada,
            "fin_jornada": fin_jornada,
            "ubic_inicio": ubic_inicio,
            "ubic_fin": ubic_fin,
            "ubic_principal": ubic_principal,
            "numero_paradas": numero_paradas,
            "horas_trabajo": horas_trabajo,
            "horas_conduccion": horas_conduccion,
            "horas_ralenti": horas_ralenti,
            "horas_descanso": horas_descanso,
            "horas_pausa": horas_pausa,
            "horas_extra": horas_extra
        })

    kpis = pd.DataFrame(kpis_list).round(2)
    kpis = kpis.sort_values(by=["conductor", "fecha"])

    # FORMATO HORAS
    kpis["inicio_jornada"] = pd.to_datetime(kpis["inicio_jornada"]).dt.strftime("%I:%M %p").str.lstrip("0")
    kpis["fin_jornada"] = pd.to_datetime(kpis["fin_jornada"]).dt.strftime("%I:%M %p").str.lstrip("0")

    st.subheader("Resumen por conductor")
    st.dataframe(kpis)

    # ==============================
    # EXPORTAR EXCEL
    # ==============================

    def limpiar_nombre(nombre):
        if pd.isna(nombre):
            return "SinNombre"
        return re.sub(r'[\\/*?:\\[\\]]', "", str(nombre))[:31]

    buffer = io.BytesIO()

    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:

        for conductor, df_conductor in kpis.groupby("conductor"):

            nombre_hoja = limpiar_nombre(conductor)

            df_conductor.to_excel(writer, sheet_name=nombre_hoja, index=False)

            ws = writer.sheets[nombre_hoja]

            for i, col in enumerate(df_conductor.columns):
                try:
                    max_len = max(df_conductor[col].astype(str).apply(len).max(), len(col))
                except:
                    max_len = len(col)

                ws.column_dimensions[chr(65 + i)].width = max_len + 2

            # HOJA BLOQUES
            bloques_cond = bloques[
                bloques["vehiculo"].isin(df_conductor["vehiculo"])
            ].copy()

            nombre_bloques = limpiar_nombre(f"Bloques {conductor}")

            bloques_cond.to_excel(writer, sheet_name=nombre_bloques, index=False)

            ws2 = writer.sheets[nombre_bloques]

            for i, col in enumerate(bloques_cond.columns):
                try:
                    max_len = max(bloques_cond[col].astype(str).apply(len).max(), len(col))
                except:
                    max_len = len(col)

                ws2.column_dimensions[chr(65 + i)].width = max_len + 2

    st.download_button(
        label="Descargar reporte Excel",
        data=buffer,
        file_name="reporte_jornada_conductores.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )