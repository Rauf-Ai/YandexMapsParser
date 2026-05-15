"""Quick offline test for _extract_vitrina strategies."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from bs4 import BeautifulSoup
from yandex_parser import _extract_vitrina

def make_soup(text: str) -> BeautifulSoup:
    return BeautifulSoup(f"<html><body><p>{text}</p></body></html>", "html.parser")

rub = "₽"
nbsp = "\xa0"

cases = [
    ("Витрина",
     f"Витрина Маникюр без покрытия1{nbsp}800{nbsp}{rub}Маникюр гель-лак2{nbsp}500{nbsp}{rub}Педикюр3{nbsp}000{nbsp}{rub}"),
    ("Прайс-лист",
     f"Прайс-лист Стрижка детская500{nbsp}{rub}Стрижка мужская800{nbsp}{rub}Окрашивание2{nbsp}500{nbsp}{rub}"),
    ("Услуги",
     f"Услуги Чистка зубов2{nbsp}000{nbsp}{rub}Пломба от3{nbsp}000{nbsp}{rub}Удаление зуба5{nbsp}000{nbsp}{rub}"),
    ("Цены",
     f"Цены Уход за кожей лица3{nbsp}500{nbsp}{rub}Массаж спины2{nbsp}000{nbsp}{rub}Обертывание4{nbsp}000{nbsp}{rub}"),
    ("Кластер (без заголовка)",
     f"Некий вводный текст тут Маникюр без покрытия1{nbsp}800{nbsp}{rub}Маникюр гель-лак2{nbsp}500{nbsp}{rub}Педикюр3{nbsp}000{nbsp}{rub}Шеллак3{nbsp}500{nbsp}{rub}"),
    ("Без услуг (ожидается пусто)",
     "Просто текст без цен и услуг"),
]

ok = True
for label, text in cases:
    svcs, pr = _extract_vitrina(make_soup(text))
    status = "OK" if (svcs or label == "Без услуг (ожидается пусто)") else "FAIL"
    if status == "FAIL":
        ok = False
    print(f"[{status}] {label}: {len(svcs)} услуг, pr={pr!r}")
    for s in svcs[:3]:
        print(f"       {s}")

sys.exit(0 if ok else 1)
