
from weasyprint import HTML

HTML("resumo.html").write_pdf("resumo.pdf")

print("PDF gerado com sucesso!")
