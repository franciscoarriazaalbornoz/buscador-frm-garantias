import openpyxl, json, sys
wb = openpyxl.load_workbook(sys.argv[1], read_only=True, data_only=True)
out = []
for r in list(wb['BD FRM PASAJEROS'].iter_rows(values_only=True))[1:]:
    if not r[4]: continue
    out.append({"g": r[1].strip(), "d": " ".join(str(r[2]).split()), "t": r[3], "c": str(r[4]).strip()})
json.dump(out, open('frm.json','w'), ensure_ascii=False)
print(len(out))
