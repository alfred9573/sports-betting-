"""Conversion del cron de hora CDMX a la hora de la maquina."""

from betbot.cronhora import convertir_bloque, convertir_linea

UTC = 360   # UTC va 6 horas por delante de CDMX


def test_sin_desfase_no_toca_nada():
    assert convertir_linea("30 10 * * 0 cmd", 0) == ["30 10 * * 0 cmd"]


def test_servidor_en_utc():
    assert convertir_linea("30 10 * * 0 cmd x", UTC) == ["30 16 * * 0 cmd x"]


def test_cruzar_medianoche_cambia_el_dia():
    """Domingo, lunes y jueves 18:00 CDMX = lunes, martes y viernes 00:00 UTC."""
    assert convertir_linea("0 18 * * 0,1,4 cmd", UTC) == ["0 0 * * 1,2,5 cmd"]
    assert convertir_linea("0 18 * * 6 cmd", UTC) == ["0 0 * * 0 cmd"]   # sabado -> domingo


def test_horas_que_caen_en_dias_distintos_se_parten():
    assert convertir_linea("5 8,20 * * * cmd", UTC) == ["5 14 * * * cmd", "5 2 * * * cmd"]


def test_desfase_negativo():
    """Una maquina en hora del Pacifico (UTC-7) va una hora por detras de CDMX."""
    assert convertir_linea("5 0 * * 1 cmd", -60) == ["5 23 * * 0 cmd"]


def test_media_hora():
    assert convertir_linea("45 16 * * * cmd", 330 + 360) == ["15 4 * * * cmd"]


def test_comentarios_intervalos_y_variables_intactos():
    for linea in ("# comentario 10 * * *", "*/10 * * * * cmd", "SHELL=/bin/bash", ""):
        assert convertir_linea(linea, UTC) == [linea]


def test_el_comando_se_conserva_entero():
    linea = "15 9 * * 6 /x/betbot-cron.sh collect --markets '' --props --horas 48"
    (out,) = convertir_linea(linea, UTC)
    assert out.endswith("collect --markets '' --props --horas 48")
    assert out.startswith("15 15 * * 6 ")


def test_rango_de_dias():
    assert convertir_linea("0 20 * * 1-5 cmd", UTC) == ["0 2 * * 2,3,4,5,6 cmd"]


def test_bloque_completo():
    bloque = "# --- betbot ---\n30 10 * * 0 a\n0 18 * * 0 b\n# --- fin betbot ---"
    assert convertir_bloque(bloque, UTC) == (
        "# --- betbot ---\n30 16 * * 0 a\n0 0 * * 1 b\n# --- fin betbot ---")
