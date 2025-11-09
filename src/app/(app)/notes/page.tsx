import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";

export default function NotesPage() {
  return (
    <div className="space-y-6">
      <div>
        <p className="text-sm uppercase tracking-wider text-muted-foreground">
          Notas
        </p>
        <h1 className="text-3xl font-semibold">Central de notas</h1>
        <p className="text-muted-foreground">
          Crie resumos privados por módulo ou problema e compartilhe com o time
          quando quiser.
        </p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Novo rascunho</CardTitle>
          <CardDescription>Escreva em Markdown. O preview chegará em breve.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <Textarea placeholder="Insights sobre Rectangular Pasture..." rows={6} />
          <div className="flex justify-end gap-2">
            <Button variant="secondary">Cancelar</Button>
            <Button>Salvar nota</Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
